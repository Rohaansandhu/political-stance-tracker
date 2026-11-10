"""
Fetch the amendments behind amendment roll calls and join them to `rollcall_votes`.

Amendment roll calls (`category: "amendment"`) already live in `rollcall_votes`, but they
only say which parent bill was amended. This script asks the Congress.gov API which
amendment each roll call was on, and upserts one doc per voted amendment into the
`amendments` collection. No LLM calls.

Needs CONGRESS_API_KEY in .env (free from https://api.data.gov/signup/).

Usage:
    python3 src/get_amendments.py --bill_ids hr2799-118 --report
    python3 src/get_amendments.py --congress 118 --chamber house --report
"""

import argparse
import csv
import json
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import db.db_utils as db_utils
from utils.vote_utils import build_bill_id

load_dotenv(".env")

API_BASE = "https://api.congress.gov/v3"
CACHE_DIR = Path("data/congress_api_cache")
REPORT_DIR = Path("data/amendments")
OUTPUT_COLLECTION = "amendments"
PAGE_LIMIT = 250

# Same bill types as process_votes_by_member: legislation, not resolutions
BILL_TYPES = {"hr", "hjres", "s", "sjres"}
CHAMBER_CODES = {"house": "h", "senate": "s"}

# Roll call dates are Eastern time, while the API reports recorded votes in UTC
EASTERN = ZoneInfo("America/New_York")

# Wording in an amendment's latestAction that means it got a roll call vote
ROLL_CALL_RE = re.compile(r"roll no\.|record vote|recorded vote|yea-nay", re.IGNORECASE)

# --- pure helpers (tested in tests/test_amendments.py) ---


def build_vote_id(recorded_vote):
    """
    rollcall_votes vote_id for one entry of an amendment action's `recordedVotes`.
    {"chamber": "House", "congress": 118, "rollNumber": 75, "date": "2024-03-08T15:35:43Z"}
        -> "h75-118.2024"
    """
    chamber = recorded_vote["chamber"][0].lower()
    date = datetime.fromisoformat(recorded_vote["date"].replace("Z", "+00:00"))
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    year = date.astimezone(EASTERN).year
    return f"{chamber}{recorded_vote['rollNumber']}-{recorded_vote['congress']}.{year}"


def extract_vote_ids(actions):
    """Unique vote_ids across an amendment's actions (the same vote repeats on several)."""
    vote_ids = []
    for action in actions:
        for recorded_vote in action.get("recordedVotes") or []:
            vote_id = build_vote_id(recorded_vote)
            if vote_id not in vote_ids:
                vote_ids.append(vote_id)
    return vote_ids


def build_amendment_id(amendment_type, number, congress):
    """("HAMDT", "858", 118) -> "hamdt858-118", the same id build_bill_id gives."""
    return build_bill_id(
        {"type": amendment_type.lower(), "number": int(number), "congress": int(congress)}
    )


def get_vote_direction(rollcall):
    """-1 when a YES on the roll call is a vote against the amendment (motion to table)."""
    text = f"{rollcall.get('question', '')} {rollcall.get('type', '')}".lower()
    return -1 if "table" in text else 1


def match_votes(amendment_vote_ids, target_vote_ids):
    """
    Join amendments to roll calls.

    Args:
        amendment_vote_ids: {amendment_id: [vote_id, ...]} from each amendment's actions
        target_vote_ids: the amendment roll calls we want to explain

    Returns:
        (matched, shared): matched is {vote_id: amendment_id} for roll calls on exactly one
        amendment; shared is {vote_id: [amendment_ids]} for roll calls on several (en bloc),
        which are dropped from matched.
    """
    by_vote = defaultdict(list)
    for amendment_id, vote_ids in amendment_vote_ids.items():
        for vote_id in vote_ids:
            if vote_id in target_vote_ids and amendment_id not in by_vote[vote_id]:
                by_vote[vote_id].append(amendment_id)

    matched = {v: ids[0] for v, ids in by_vote.items() if len(ids) == 1}
    shared = {v: sorted(ids) for v, ids in by_vote.items() if len(ids) > 1}
    return matched, shared


def normalize_name(name):
    """Lowercase ASCII with straight apostrophes, for comparing member names."""
    name = name.replace("\u2019", "'")
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()


def sponsor_matches(rollcall, detail):
    """
    House sanity check: the roll call's amendment.author ("Lynch of Massachusetts Part B
    Amendment No. 8") should name the API's sponsor. None when there's nothing to compare.
    (Amendment numbers can't be compared: the roll call's, the description's, and the
    "(A008)" in action text each count differently.)
    """
    author = (rollcall.get("amendment") or {}).get("author")
    sponsors = detail.get("sponsors") or []
    if not author or not sponsors or not sponsors[0].get("lastName"):
        return None
    return normalize_name(sponsors[0]["lastName"]) in normalize_name(author)


def bill_id_from_api(bill):
    """Congress.gov amendedBill object -> our bill_id, or None."""
    if not bill:
        return None
    return build_amendment_id(bill["type"], bill["number"], bill["congress"])


# --- Congress.gov API ---


class CongressApi:
    """Throttled, cached GET requests to the Congress.gov API."""

    def __init__(self, force=False):
        self.api_key = os.getenv("CONGRESS_API_KEY")
        if not self.api_key:
            raise SystemExit("CONGRESS_API_KEY is missing from .env")
        self.force = force
        # Free keys allow 5,000 requests/hour, so default to one every 0.75s
        self.min_interval = float(os.getenv("CONGRESS_API_MIN_INTERVAL", "0.75"))
        self.last_request = 0.0
        self.requests_made = 0
        self.session = requests.Session()

    def cache_path(self, path, offset):
        return CACHE_DIR / f"{path}_offset{offset}.json"

    def get(self, path, offset=0):
        """GET /{path} (one page). Returns the parsed JSON, or None on a 404."""
        cache_file = self.cache_path(path, offset)
        if cache_file.exists() and not self.force:
            return json.loads(cache_file.read_text())

        params = {
            "api_key": self.api_key,
            "format": "json",
            "limit": PAGE_LIMIT,
            "offset": offset,
        }
        backoff = 30
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            self.requests_made += 1

            try:
                response = self.session.get(f"{API_BASE}/{path}", params=params, timeout=60)
            except requests.RequestException as e:
                print(f"  {path}: request failed ({e}), retrying in {backoff}s")
                time.sleep(backoff)
                backoff *= 2
                continue

            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                print(f"  {path}: HTTP {response.status_code}, retrying in {backoff}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            response.raise_for_status()

            data = response.json()
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
            return data

        raise RuntimeError(f"Congress.gov API kept failing for {path}")

    def get_all(self, path, key):
        """Every item of a paged list endpoint, e.g. key="amendments"."""
        items = []
        offset = 0
        while True:
            data = self.get(path, offset)
            if data is None:
                return items
            page = data.get(key) or []
            items.extend(page)
            count = (data.get("pagination") or {}).get("count", len(items))
            offset += PAGE_LIMIT
            if not page or offset >= count:
                return items

    def bill_amendments(self, bill):
        return self.get_all(
            f"bill/{bill['congress']}/{bill['type']}/{bill['number']}/amendments",
            "amendments",
        )

    def amendment_actions(self, congress, amendment_type, number):
        return self.get_all(
            f"amendment/{congress}/{amendment_type.lower()}/{number}/actions", "actions"
        )

    def amendment_detail(self, congress, amendment_type, number):
        data = self.get(f"amendment/{congress}/{amendment_type.lower()}/{number}")
        return (data or {}).get("amendment")


# --- join ---


def load_amendment_rollcalls(congress=None, chamber=None, bill_ids=None):
    """Amendment roll calls on legislation, grouped by parent bill_id: {bill_id: [rollcall]}."""
    query = {"category": "amendment", "bill.type": {"$in": sorted(BILL_TYPES)}}
    if congress:
        query["congress"] = int(congress)
    if chamber:
        query["chamber"] = CHAMBER_CODES[chamber]

    by_bill = defaultdict(list)
    projection = {"votes": 0}
    for rollcall in db_utils.get_collection("rollcall_votes").find(query, projection):
        bill_id = build_bill_id(rollcall["bill"])
        if bill_ids and bill_id not in bill_ids:
            continue
        by_bill[bill_id].append(rollcall)
    return by_bill


def parse_listed(listed):
    """(congress, type, number) of an amendment from a bill's amendments list."""
    return listed["congress"], listed["type"], listed["number"]


def collect_vote_ids(api, keys, cache):
    """Fetch actions for amendment `keys` not yet in `cache` ({amendment_id: (vote_ids, actions)})."""
    for congress, amendment_type, number in keys:
        amendment_id = build_amendment_id(amendment_type, number, congress)
        if amendment_id in cache:
            continue
        actions = api.amendment_actions(congress, amendment_type, number)
        cache[amendment_id] = (extract_vote_ids(actions), actions)


def join_bill(api, bill_id, rollcalls):
    """
    Match one parent bill's amendment roll calls to amendments.

    Returns:
        (matched, shared): see match_votes
    """
    target = {r["vote_id"] for r in rollcalls}
    bill = rollcalls[0]["bill"]
    listed = api.bill_amendments(bill)

    # Fetch actions for amendments whose latest action mentions a roll call first,
    # then for the rest only if some roll calls are still unexplained
    flagged = [a for a in listed if ROLL_CALL_RE.search((a.get("latestAction") or {}).get("text", ""))]
    others = [a for a in listed if a not in flagged]

    fetched = {}
    collect_vote_ids(api, [parse_listed(a) for a in flagged], fetched)
    matched, shared = match_votes({k: v[0] for k, v in fetched.items()}, target)

    if target - set(matched) - set(shared) and others:
        collect_vote_ids(api, [parse_listed(a) for a in others], fetched)
        matched, shared = match_votes({k: v[0] for k, v in fetched.items()}, target)

    # Senate roll calls name their S.Amdt directly, which catches amendments the bill's
    # list misses (e.g. amendments to a House message rather than to the bill itself)
    for rollcall in rollcalls:
        vote_id = rollcall["vote_id"]
        amendment = rollcall.get("amendment") or {}
        if vote_id in matched or vote_id in shared:
            continue
        if rollcall["chamber"] != "s" or amendment.get("type") != "s" or not amendment.get("number"):
            continue
        collect_vote_ids(api, [(rollcall["congress"], "samdt", amendment["number"])], fetched)
        matched, shared = match_votes({k: v[0] for k, v in fetched.items()}, target)

    return matched, shared


def build_amendment_doc(api, amendment_id, detail, parent_bill_id, vote_entries):
    """The `amendments` doc for one matched amendment."""
    amended_amendment = detail.get("amendedAmendment")
    amended_amendment_id = None
    amended_amendment_purpose = None
    if amended_amendment:
        amended_amendment_id = build_amendment_id(
            amended_amendment["type"], amended_amendment["number"], amended_amendment["congress"]
        )
        # Fetched so the analysis prompt can say what the amendment amends
        parent = api.amendment_detail(
            amended_amendment["congress"], amended_amendment["type"], amended_amendment["number"]
        ) or {}
        amended_amendment_purpose = parent.get("purpose") or parent.get("description")

    amended_bill_id = bill_id_from_api(detail.get("amendedBill")) or parent_bill_id
    if amended_bill_id != parent_bill_id:
        print(
            f"  WARNING: {amendment_id} amends {amended_bill_id} per the API, "
            f"but its roll call is on {parent_bill_id}; using {parent_bill_id}"
        )

    sponsors = detail.get("sponsors") or []
    vote_entries = sorted(vote_entries, key=lambda v: v["vote_id"])

    return {
        "amendment_id": amendment_id,
        "congress": str(detail["congress"]),
        "chamber": "h" if detail["type"].upper() == "HAMDT" else "s",
        "type": detail["type"].lower(),
        "number": int(detail["number"]),
        # The roll call's parent bill is authoritative, so profiles filter consistently
        "amended_bill_id": parent_bill_id,
        "amended_amendment_id": amended_amendment_id,
        "amended_amendment_purpose": amended_amendment_purpose,
        "purpose": detail.get("purpose"),
        "description": detail.get("description"),
        # Stored for reference, never sent to the LLM
        "sponsor_bioguide": sponsors[0].get("bioguideId") if sponsors else None,
        "vote_ids": [v["vote_id"] for v in vote_entries],
        "votes": vote_entries,
        "source": "congress.gov",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_amendments(congress=None, chamber=None, bill_ids=None, force=False):
    """
    Join every in-scope amendment roll call and upsert the `amendments` collection.

    Returns a list of per-roll-call result rows for the report.
    """
    api = CongressApi(force=force)
    by_bill = load_amendment_rollcalls(congress, chamber, bill_ids)
    total = sum(len(r) for r in by_bill.values())
    print(f"Found {total} amendment roll calls on {len(by_bill)} bills")

    bill_data = db_utils.get_collection("bill_data")
    rows = []
    for i, (bill_id, rollcalls) in enumerate(sorted(by_bill.items()), 1):
        print(f"[{i}/{len(by_bill)}] {bill_id}: {len(rollcalls)} roll calls")
        rollcalls_by_id = {r["vote_id"]: r for r in rollcalls}
        try:
            matched, shared = join_bill(api, bill_id, rollcalls)
        except Exception as e:
            print(f"  ERROR joining {bill_id}: {e}")
            matched, shared = {}, {}

        bill_doc = bill_data.find_one({"bill_id": bill_id}, {"summary.text": 1})
        has_summary = bool(((bill_doc or {}).get("summary") or {}).get("text"))

        votes_by_amendment = defaultdict(list)
        for vote_id, amendment_id in matched.items():
            rollcall = rollcalls_by_id[vote_id]
            votes_by_amendment[amendment_id].append(
                {
                    "vote_id": vote_id,
                    "vote_direction": get_vote_direction(rollcall),
                    "question": rollcall.get("question"),
                }
            )

        for amendment_id, vote_entries in votes_by_amendment.items():
            match = re.fullmatch(r"([a-z]+)(\d+)-(\d+)", amendment_id)
            amendment_type, number, amd_congress = match.groups()
            detail = api.amendment_detail(amd_congress, amendment_type, number)
            if not detail:
                print(f"  ERROR: no detail for {amendment_id}")
                continue
            doc = build_amendment_doc(api, amendment_id, detail, bill_id, vote_entries)
            for entry in vote_entries:
                if sponsor_matches(rollcalls_by_id[entry["vote_id"]], detail) is False:
                    print(
                        f"  WARNING: {entry['vote_id']}'s author doesn't name "
                        f"{amendment_id}'s sponsor"
                    )
            db_utils.update_one(OUTPUT_COLLECTION, doc, ["amendment_id"])

        for vote_id, rollcall in rollcalls_by_id.items():
            if vote_id in matched:
                status = "matched"
            elif vote_id in shared:
                status = "shared"
            else:
                status = "unmatched"
            rows.append(
                {
                    "vote_id": vote_id,
                    "congress": rollcall["congress"],
                    "chamber": rollcall["chamber"],
                    "bill_id": bill_id,
                    "status": status,
                    "amendment_ids": matched.get(vote_id) or ",".join(shared.get(vote_id, [])),
                    "vote_direction": get_vote_direction(rollcall),
                    "parent_has_summary": has_summary,
                    "question": rollcall.get("question"),
                    "rule_number": (rollcall.get("amendment") or {}).get("number"),
                }
            )

    print(f"Congress.gov API requests made: {api.requests_made}")
    return rows


def print_report(rows):
    """Coverage per congress x chamber, plus CSVs of the roll calls that didn't match."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["congress"], row["chamber"])].append(row)

    header = (
        f"{'congress':>8} {'ch':>2} {'rollcalls':>9} {'matched':>8} {'%':>6} "
        f"{'en_bloc':>7} {'unmatched':>9} {'tabling':>7} {'no_summary':>10}"
    )
    print("\nAmendment roll call coverage")
    print(header)
    print("-" * len(header))
    totals = Counter()
    for (congress, chamber), group in sorted(groups.items()):
        counts = Counter(r["status"] for r in group)
        tabling = sum(r["vote_direction"] == -1 for r in group)
        # Matched amendments whose parent bill has no summary (the analysis pass skips them)
        no_summary = len(
            {r["amendment_ids"] for r in group if r["status"] == "matched" and not r["parent_has_summary"]}
        )
        pct = 100 * counts["matched"] / len(group)
        print(
            f"{congress:>8} {chamber:>2} {len(group):>9} {counts['matched']:>8} {pct:>5.1f}% "
            f"{counts['shared']:>7} {counts['unmatched']:>9} {tabling:>7} {no_summary:>10}"
        )
        totals.update({"all": len(group), **counts, f"all_{chamber}": len(group), f"matched_{chamber}": counts["matched"]})

    for chamber in ("h", "s"):
        if totals[f"all_{chamber}"]:
            pct = 100 * totals[f"matched_{chamber}"] / totals[f"all_{chamber}"]
            print(f"Chamber {chamber}: {totals[f'matched_{chamber}']}/{totals[f'all_{chamber}']} matched ({pct:.1f}%)")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    for status, filename in (("unmatched", "unmatched.csv"), ("shared", "en_bloc.csv")):
        path = REPORT_DIR / filename
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(r for r in rows if r["status"] == status)
        print(f"Wrote {sum(r['status'] == status for r in rows)} {status} roll calls to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--congress", type=int, help="Only this congress, e.g. 118")
    parser.add_argument("--chamber", choices=["house", "senate"])
    parser.add_argument(
        "--bill_ids", nargs="+", help="Only amendments to these parent bills (e.g. hr2799-118)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore the API cache and refetch everything"
    )
    parser.add_argument(
        "--report", action="store_true", help="Print coverage and write data/amendments/*.csv"
    )
    args = parser.parse_args()

    db_utils.ensure_indexes()
    result_rows = fetch_amendments(args.congress, args.chamber, args.bill_ids, args.force)
    if args.report:
        print_report(result_rows)
    else:
        matched_count = sum(r["status"] == "matched" for r in result_rows)
        print(f"Matched {matched_count}/{len(result_rows)} amendment roll calls")
