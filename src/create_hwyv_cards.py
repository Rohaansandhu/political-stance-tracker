"""
Build the cards behind the website's "How Would You Vote?" feature: one per bill that came to
a contested final-passage roll call, for one chamber's profile set, in the `hwyv_cards`
collection. About 40 of them are marked `featured`.

Member votes aren't copied onto cards; the website reads them from `rollcall_votes`.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from pymongo import ASCENDING, UpdateOne

import db.db_utils as db_utils
from analysis.definitions import category_names, llm_axes
from schema.hwyv_cards import HwyvCard
from utils.agreement_utils import contest_weight
from utils.vote_utils import build_bill_id, get_vote_value

PASSAGE_CATEGORIES = ["passage", "passage-suspension"]
CHAMBER_CODES = {"house": "h", "senate": "s"}
PARTIES = ("D", "R", "I")
TERM_PARTIES = {"Democrat": "D", "Republican": "R", "Independent": "I"}
DEFAULT_OVERRIDES = Path("config/hwyv_featured_overrides.json")

MIN_PLAYABLE_CONTEST = 0.2
MIN_FEATURED_CONTEST = 0.4
OMNIBUS_CATEGORY_COUNT = 5
OMNIBUS_TITLE = re.compile(r"consolidated appropriations|omnibus", re.IGNORECASE)

MIN_AXIS_CARDS = 3
AXIS_RELEVANCE = 0.5
MIN_CATEGORY_CARDS = 2
MIN_CROSS_CARDS = 6
CROSS_THRESHOLD = 0.15
D_YEA_SHARE_RANGE = (0.45, 0.55)

BRIEF_TARGET_CHARS = 200
BRIEF_MAX_CHARS = 600


# --- Reading a profile set and its roll calls ---


def load_spec(spec_hash):
    """Model and schema versions from the profile set, and the chamber from its spec_hash."""
    profile = db_utils.get_collection("legislator_profiles").find_one(
        {"spec_hash": spec_hash},
        {"model": 1, "schema_version": 1, "axis_schema_version": 1},
    )
    if profile is None:
        raise ValueError(f"No legislator profiles for spec_hash {spec_hash}")
    if profile.get("axis_schema_version") is None:
        raise ValueError(f"{spec_hash} has no axis schema, so its bills have no axis analyses")

    # Profiles don't store their chamber; get_spec_hash puts it in the hash
    match = re.search(r"_(house|senate)_", spec_hash)
    if match is None:
        raise ValueError(f"{spec_hash} is not for a single chamber")

    return {
        "spec_hash": spec_hash,
        "model": profile["model"],
        "schema_version": profile["schema_version"],
        "axis_schema_version": profile["axis_schema_version"],
        "chamber": match.group(1),
    }


def latest_congresses(chamber, count=2):
    congresses = db_utils.get_collection("rollcall_votes").distinct(
        "congress", {"chamber": CHAMBER_CODES[chamber]}
    )
    return sorted(congresses)[-count:]


def latest_rollcall_per_bill(rollcalls):
    """Keep one roll call per bill: the latest passage vote."""
    latest = {}
    for rollcall in rollcalls:
        bill_id = build_bill_id(rollcall.get("bill") or {})
        if not bill_id:
            continue
        current = latest.get(bill_id)
        if current is None or (rollcall["date"], rollcall["number"]) > (
            current["date"],
            current["number"],
        ):
            latest[bill_id] = rollcall
    return latest


def compute_vote_stats(votes):
    """The contest, party tally and party split of one roll call's `votes` field."""
    tally = {party: {"yea": 0, "nay": 0, "not_voting": 0} for party in PARTIES}
    yea = nay = 0

    for vote_key, voters in votes.items():
        value = get_vote_value(vote_key)
        side = {1: "yea", -1: "nay", 0: "not_voting"}[value]
        for voter in voters:
            # "VP" entries are plain strings, not voters
            if not isinstance(voter, dict):
                continue
            yea += value == 1
            nay += value == -1
            if voter.get("party") in tally:
                tally[voter["party"]][side] += 1

    def party_split(party):
        cast = tally[party]["yea"] + tally[party]["nay"]
        return min(tally[party]["yea"], tally[party]["nay"]) / cast if cast else 0.0

    return {
        "contest": contest_weight(yea, nay),
        "tally": tally,
        "cross": max(party_split("D"), party_split("R")),
        "d_majority_yea": tally["D"]["yea"] > tally["D"]["nay"],
        "r_majority_yea": tally["R"]["yea"] > tally["R"]["nay"],
    }


def is_omnibus(primary_categories, titles):
    return len(primary_categories) >= OMNIBUS_CATEGORY_COUNT or any(
        title and OMNIBUS_TITLE.search(title) for title in titles
    )


def build_brief(summary_text, fallback):
    """
    A short excerpt of the Library of Congress summary. Bill analyses only store a title (often
    the bill's own promotional name) and key provisions, so this is the only prose that exists.
    """
    if not summary_text:
        return fallback

    text = summary_text.replace(" ", " ")
    # The source flattens HTML lists, losing the space after each item
    text = re.sub(r"(?<=[a-z]),(?=[a-z])", ", ", text)
    text = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", text)

    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        # Notes such as "(This measure has not been amended since it was passed...)"
        if not paragraph or (paragraph.startswith("(") and paragraph.endswith(")")):
            continue
        # A leading line that's just the bill's name
        if not paragraphs and paragraph[-1] not in ".:;":
            continue
        paragraphs.append(paragraph)

    brief = ""
    for paragraph in paragraphs:
        brief = f"{brief} {paragraph}".strip()
        if len(brief) >= BRIEF_TARGET_CHARS:
            break
    if not brief:
        return fallback
    if len(brief) <= BRIEF_MAX_CHARS and brief[-1] in ".)\"”":
        return brief

    # Cut back to the last whole sentence
    sentences = re.findall(r".+?[.)](?:\s+|$)(?=[A-Z(]|$)", brief[:BRIEF_MAX_CHARS])
    return "".join(sentences).strip() or fallback


def left_right_scores(primary_categories):
    """Category -> left-right push of a Yea, scored the way member left_right scores are."""
    return {
        c["name"]: c["partisan_score"] * c["impact_score"]
        for c in primary_categories
        if c.get("partisan_score")
    }


def load_sponsor_terms(bioguide_ids):
    """bioguide id -> the member's terms."""
    legislators = db_utils.get_collection("legislators").find(
        {"bioguide": {"$in": list(bioguide_ids)}}, {"bioguide": 1, "terms": 1}
    )
    return {leg["bioguide"]: leg.get("terms", []) for leg in legislators}


def party_when(terms, date):
    """The party of the term covering `date`, or of the last term if none does."""
    if not terms:
        return None
    term = next(
        (t for t in terms if t.get("start", "") <= date <= t.get("end", "")),
        terms[-1],
    )
    return TERM_PARTIES.get(term.get("party"))


def format_sponsor(sponsor):
    """{"name": "Graves, Sam", "title": "Rep."} -> "Rep. Sam Graves"."""
    last, _, first = sponsor.get("name", "").partition(", ")
    name = f"{first} {last}".strip()
    return f"{sponsor['title']} {name}" if sponsor.get("title") else name or None


def build_card(spec, rollcall, analysis, axis_analysis, bill, sponsor_terms, stats):
    categories = analysis["political_categories"]["primary_categories"]
    primary_names = [c["name"] for c in categories]
    summary = analysis["bill_summary"]
    sponsor = bill.get("sponsor") or {}

    return {
        "spec_hash": spec["spec_hash"],
        "vote_id": rollcall["vote_id"],
        "bill_id": analysis["bill_id"],
        "chamber": spec["chamber"],
        "congress": rollcall["congress"],
        "date": rollcall["date"],
        # Senate joint resolutions often have no short title
        "short_title": bill.get("short_title") or summary["title"],
        "popular_title": bill.get("popular_title"),
        "brief": build_brief((bill.get("summary") or {}).get("text"), summary["title"]),
        "key_provisions": summary["key_provisions"],
        "needs_two_thirds": rollcall["category"] == "passage-suspension",
        "omnibus": is_omnibus(
            primary_names,
            [bill.get("short_title"), bill.get("popular_title"), summary["title"]],
        ),
        "contest": round(stats["contest"], 5),
        "cross": round(stats["cross"], 5),
        "featured": False,
        "primary_categories": primary_names,
        "result": rollcall["result"],
        "requires": rollcall.get("requires"),
        "tally": stats["tally"],
        "d_majority_yea": stats["d_majority_yea"],
        "r_majority_yea": stats["r_majority_yea"],
        "sponsor": format_sponsor(sponsor) if sponsor else None,
        "sponsor_party": party_when(sponsor_terms, rollcall["date"][:10]),
        "source_url": rollcall.get("source_url"),
        "left_right": left_right_scores(categories),
        "axes": axis_analysis["axis_scores"],
        "model": spec["model"],
        "schema_version": spec["schema_version"],
        "axis_schema_version": spec["axis_schema_version"],
    }


def build_playable_cards(spec, congresses):
    rollcalls = db_utils.get_collection("rollcall_votes").find(
        {
            "chamber": CHAMBER_CODES[spec["chamber"]],
            "congress": {"$in": congresses},
            "category": {"$in": PASSAGE_CATEGORIES},
        }
    )
    latest = latest_rollcall_per_bill(rollcalls)
    bill_ids = list(latest)

    analyses = {
        doc["bill_id"]: doc
        for doc in db_utils.get_collection("bill_analyses").find(
            {
                "bill_id": {"$in": bill_ids},
                "model": spec["model"],
                "schema_version": spec["schema_version"],
            }
        )
    }
    axis_analyses = {
        doc["bill_id"]: doc
        for doc in db_utils.get_collection("bill_axis_analyses").find(
            {
                "bill_id": {"$in": bill_ids},
                "model": spec["model"],
                "axis_schema_version": spec["axis_schema_version"],
            }
        )
    }
    analyzed = [b for b in bill_ids if b in analyses and b in axis_analyses]

    bills = {
        doc["bill_id"]: doc
        for doc in db_utils.get_collection("bill_data").find(
            {"bill_id": {"$in": analyzed}},
            {"bill_id": 1, "short_title": 1, "popular_title": 1, "summary": 1, "sponsor": 1},
        )
    }
    sponsor_terms = load_sponsor_terms(
        {
            (b.get("sponsor") or {}).get("bioguide_id")
            for b in bills.values()
            if (b.get("sponsor") or {}).get("bioguide_id")
        }
    )

    cards = []
    for bill_id in analyzed:
        stats = compute_vote_stats(latest[bill_id].get("votes", {}))
        if stats["contest"] < MIN_PLAYABLE_CONTEST:
            continue
        bill = bills.get(bill_id, {})
        terms = sponsor_terms.get((bill.get("sponsor") or {}).get("bioguide_id"), [])
        cards.append(
            build_card(
                spec,
                latest[bill_id],
                analyses[bill_id],
                axis_analyses[bill_id],
                bill,
                terms,
                stats,
            )
        )

    print(
        f"{spec['spec_hash']}: {len(bill_ids)} bills with a passage vote in {congresses}, "
        f"{len(analyzed)} analyzed, {len(cards)} playable (contest >= {MIN_PLAYABLE_CONTEST})"
    )
    return cards


# --- Featured selection ---


def rank_score(card):
    return card["contest"] * (1 + 2 * card["cross"])


def rank_cards(cards):
    """Best first: by rank score, newest first on ties."""
    by_recency = sorted(cards, key=lambda c: (c["date"], c["vote_id"]), reverse=True)
    return sorted(by_recency, key=rank_score, reverse=True)


def covers_axis(card, axis):
    return any(
        a["axis"] == axis and a["relevance"] >= AXIS_RELEVANCE for a in card["axes"]
    )


def yea_side(card):
    """Which party alone had a Yea majority ("D" or "R"), or None if not exactly one did."""
    if card["d_majority_yea"] == card["r_majority_yea"]:
        return None
    return "D" if card["d_majority_yea"] else "R"


def d_yea_share(cards):
    sides = Counter(yea_side(c) for c in cards)
    split = sides["D"] + sides["R"]
    return sides["D"] / split if split else None


def coverage_counts(cards, axes, categories):
    return {
        "axes": {a: sum(covers_axis(c, a) for c in cards) for a in axes},
        "categories": {
            name: sum(name in c["primary_categories"] for c in cards) for name in categories
        },
        "cross": sum(c["cross"] >= CROSS_THRESHOLD for c in cards),
    }


def swap_keeps_coverage(before, after):
    """False if a swap takes any rule that's met further from being met, or breaks it."""

    def kept(old, new, required):
        return new >= min(old, required)

    return (
        all(
            kept(before["axes"][a], after["axes"][a], MIN_AXIS_CARDS) for a in before["axes"]
        )
        and all(
            kept(before["categories"][c], after["categories"][c], MIN_CATEGORY_CARDS)
            for c in before["categories"]
        )
        and kept(before["cross"], after["cross"], MIN_CROSS_CARDS)
    )


def fill_coverage(selected, ranked, target, requirement, items, helps):
    """
    Add the top-ranked unused card that helps the most under-covered item, until every item
    has `requirement` cards, no card helps, or the deck is full.
    """
    counts = {item: sum(helps(c, item) for c in selected) for item in items}
    while len(selected) < target:
        chosen = None
        for item in sorted(
            (i for i in counts if counts[i] < requirement), key=lambda i: counts[i]
        ):
            chosen = next(
                (c for c in ranked if c not in selected and helps(c, item)), None
            )
            if chosen is not None:
                break
        if chosen is None:
            return
        selected.append(chosen)
        for item in counts:
            counts[item] += helps(chosen, item)


def balance_parties(selected, ranked, axes, categories):
    """
    Swap the lowest-ranked card on the over-represented side for the highest-ranked unused card
    on the other side, until the D-Yea share is in range or no swap helps without breaking
    coverage. Modifies `selected`.
    """
    low, high = D_YEA_SHARE_RANGE
    order = {c["vote_id"]: i for i, c in enumerate(ranked)}

    while True:
        share = d_yea_share(selected)
        if share is None or low <= share <= high:
            return

        over, under = ("D", "R") if share > high else ("R", "D")
        before = coverage_counts(selected, axes, categories)
        removable = sorted(
            (c for c in selected if yea_side(c) == over),
            key=lambda c: order[c["vote_id"]],
            reverse=True,
        )
        unused = [c for c in ranked if c not in selected and yea_side(c) == under]

        swap = None
        for out in removable:
            for incoming in unused:
                trial = [c for c in selected if c is not out] + [incoming]
                if swap_keeps_coverage(before, coverage_counts(trial, axes, categories)):
                    swap = (out, incoming)
                    break
            if swap:
                break
        if swap is None:
            return

        selected.remove(swap[0])
        selected.append(swap[1])


def select_featured(cards, axes, categories, target):
    """vote_ids of the auto-picked featured deck, in selection order."""
    candidates = [
        c for c in cards if c["contest"] >= MIN_FEATURED_CONTEST and not c["omnibus"]
    ]
    ranked = rank_cards(candidates)
    selected = []

    fill_coverage(selected, ranked, target, MIN_AXIS_CARDS, axes, covers_axis)
    fill_coverage(
        selected,
        ranked,
        target,
        MIN_CATEGORY_CARDS,
        categories,
        lambda c, name: name in c["primary_categories"],
    )
    fill_coverage(
        selected,
        ranked,
        target,
        MIN_CROSS_CARDS,
        ["cross"],
        lambda c, _: c["cross"] >= CROSS_THRESHOLD,
    )
    for card in ranked:
        if len(selected) >= target:
            break
        if card not in selected:
            selected.append(card)

    balance_parties(selected, ranked, axes, categories)
    return [c["vote_id"] for c in selected]


def load_overrides(path, chamber):
    """The {"include": [...], "exclude": [...]} block for a chamber, if the file exists."""
    if path is None or not Path(path).exists():
        return {"include": [], "exclude": []}
    with open(path, "r") as f:
        block = json.load(f).get(chamber, {})
    return {"include": block.get("include", []), "exclude": block.get("exclude", [])}


def apply_overrides(selected_ids, overrides, cards):
    """Overrides win over the algorithm. Returns (featured vote_ids, warnings)."""
    playable = {c["vote_id"] for c in cards}
    warnings = [
        f"Override {vote_id} isn't a playable card, so it was ignored"
        for vote_id in overrides["include"]
        if vote_id not in playable
    ]
    excluded = set(overrides["exclude"])
    featured = [v for v in selected_ids if v not in excluded]
    for vote_id in overrides["include"]:
        if vote_id in playable and vote_id not in excluded and vote_id not in featured:
            featured.append(vote_id)
    return featured, warnings


def build_report(featured, cards, axes, categories, target):
    """Coverage of the final featured set, and each rule it doesn't meet, with the reason."""
    candidates = [
        c for c in cards if c["contest"] >= MIN_FEATURED_CONTEST and not c["omnibus"]
    ]
    counts = coverage_counts(featured, axes, categories)
    available = coverage_counts(candidates, axes, categories)
    sides = Counter(yea_side(c) for c in featured)
    share = d_yea_share(featured)
    low, high = D_YEA_SHARE_RANGE
    unmet = []

    if len(featured) < target:
        unmet.append(
            f"Target of {target} cards not reached: {len(featured)} featured from "
            f"{len(candidates)} candidates"
        )
    for axis, count in counts["axes"].items():
        if count < MIN_AXIS_CARDS:
            unmet.append(
                f"Axis {axis}: {count} of {MIN_AXIS_CARDS} cards "
                f"({available['axes'][axis]} candidates exist)"
            )
    for name, count in counts["categories"].items():
        if count < MIN_CATEGORY_CARDS:
            unmet.append(
                f"Category {name}: {count} of {MIN_CATEGORY_CARDS} cards "
                f"({available['categories'][name]} candidates exist)"
            )
    if counts["cross"] < MIN_CROSS_CARDS:
        unmet.append(
            f"Cross-party: {counts['cross']} of {MIN_CROSS_CARDS} cards "
            f"({available['cross']} candidates exist)"
        )
    if share is None or not low <= share <= high:
        pool = Counter(yea_side(c) for c in candidates)
        unmet.append(
            f"Party balance: D-Yea share is "
            f"{'undefined' if share is None else format(share, '.0%')}, outside "
            f"{low:.0%}-{high:.0%} (candidates: {pool['D']} D-Yea, {pool['R']} R-Yea)"
        )

    return {
        "counts": counts,
        "d_yea": sides["D"],
        "r_yea": sides["R"],
        "unmet": unmet,
    }


def print_report(report, featured_count, playable_count):
    print(f"\nFeatured {featured_count} of {playable_count} playable cards")
    print("\nCards per axis (relevance >= 0.5):")
    for axis, count in report["counts"]["axes"].items():
        print(f"  {axis:<18}{count}")
    print("\nCards per category:")
    for name, count in report["counts"]["categories"].items():
        print(f"  {name:<36}{count}")
    print(f"\nD-Yea / R-Yea split: {report['d_yea']} / {report['r_yea']}")
    print(f"Cross-party cards (cross >= {CROSS_THRESHOLD}): {report['counts']['cross']}")

    if report["unmet"]:
        print("\nRules not met:")
        for rule in report["unmet"]:
            print(f"  - {rule}")
    else:
        print("\nAll rules met.")


# --- Writing ---


def existing_cards(spec_hash):
    return {
        doc["vote_id"]: doc
        for doc in db_utils.get_collection("hwyv_cards").find({"spec_hash": spec_hash})
    }


def card_changed(card, stored):
    if stored is None:
        return True
    old = {k: v for k, v in stored.items() if k not in ("_id", "last_modified")}
    return old != card


def write_cards(spec_hash, cards, dry_run):
    """
    Upsert changed cards and remove ones that are no longer playable. Unchanged cards keep their
    last_modified, so a re-run doesn't rewrite them in the cloud.
    """
    stored = existing_cards(spec_hash)
    now = datetime.utcnow()
    actions = []

    for card in cards:
        if not card_changed(card, stored.get(card["vote_id"])):
            continue
        HwyvCard.model_validate({**card, "last_modified": now})
        actions.append(
            UpdateOne(
                {"spec_hash": spec_hash, "vote_id": card["vote_id"]},
                {
                    "$set": {**card, "last_modified": now},
                    # Stable across a removal and re-add, so the cloud sync can't conflict on it
                    "$setOnInsert": {"_id": f"{spec_hash}:{card['vote_id']}"},
                },
                upsert=True,
            )
        )
    removed = [v for v in stored if v not in {c["vote_id"] for c in cards}]

    print(f"\nCards to write: {len(actions)}. Cards to remove: {len(removed)}.")
    if dry_run:
        print("Dry run: nothing written.")
        return

    collection = db_utils.get_collection("hwyv_cards")
    collection.create_index(
        [("spec_hash", ASCENDING), ("vote_id", ASCENDING)], unique=True
    )
    if actions:
        db_utils.bulk_write("hwyv_cards", actions)
    if removed:
        collection.delete_many({"spec_hash": spec_hash, "vote_id": {"$in": removed}})
    print("Done.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        required=True,
        help="Profile set to build cards for. Gives the model, schema versions and chamber.",
    )
    parser.add_argument(
        "--congresses",
        type=int,
        nargs="+",
        help="Congresses to take roll calls from (default: the latest 2)",
    )
    parser.add_argument(
        "--featured_count", type=int, default=40, help="Target size of the featured deck"
    )
    parser.add_argument(
        "--overrides",
        default=DEFAULT_OVERRIDES,
        help=f"JSON file of per-chamber include/exclude vote_ids (default: {DEFAULT_OVERRIDES})",
    )
    parser.add_argument(
        "--check", action="store_true", help="Print the coverage report and don't write"
    )
    parser.add_argument(
        "--dry_run", action="store_true", help="Report what would be written without writing"
    )
    args = parser.parse_args()

    spec = load_spec(args.spec_hash)
    congresses = args.congresses or latest_congresses(spec["chamber"])
    cards = build_playable_cards(spec, congresses)

    axes = [a["id"] for a in llm_axes()]
    categories = category_names()
    selected = select_featured(cards, axes, categories, args.featured_count)
    featured_ids, warnings = apply_overrides(
        selected, load_overrides(args.overrides, spec["chamber"]), cards
    )
    for warning in warnings:
        print(f"WARNING: {warning}")

    featured_set = set(featured_ids)
    for card in cards:
        card["featured"] = card["vote_id"] in featured_set

    report = build_report(
        [c for c in cards if c["featured"]], cards, axes, categories, args.featured_count
    )
    print_report(report, len(featured_ids), len(cards))

    if args.check:
        return

    write_cards(args.spec_hash, cards, args.dry_run)


if __name__ == "__main__":
    main()
