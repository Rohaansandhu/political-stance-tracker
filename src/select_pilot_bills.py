"""
Pick a pilot set of bills for trying out the LLM analysis passes.

The set is every validation bill that has a summary (see validate_axes.py) plus a seeded
sample spread evenly across congresses and bill types. Prints the ids and saves them to
data/pilot_bill_ids.txt, ready for `--bill_ids $(cat data/pilot_bill_ids.txt)`.
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import db.db_utils as db_utils
from analysis.definitions import DEFINITIONS_DIR

OUTPUT_FILE = Path("data/pilot_bill_ids.txt")


def load_validation_bill_ids():
    with open(DEFINITIONS_DIR / "validation_bills.json") as f:
        return [case["bill_id"] for case in json.load(f)["cases"]]


def find_eligible_bills():
    """(bill_id, congress, bill_type) of every bill with summary text to analyze."""
    bills = db_utils.get_collection("bill_data").find(
        {"bill_type": {"$in": ["hr", "hjres", "s", "sjres"]}},
        {"bill_id": 1, "congress": 1, "bill_type": 1, "summary.text": 1},
    )
    return [
        (b["bill_id"], str(b["congress"]), b["bill_type"])
        for b in bills
        if ((b.get("summary") or {}).get("text") or "").strip()
    ]


def spread_sample(bills, count, seed):
    """Take `count` bills round-robin across (congress, bill_type) groups."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for bill_id, congress, bill_type in bills:
        groups[(congress, bill_type)].append(bill_id)
    for group in groups.values():
        rng.shuffle(group)

    picked = []
    group_order = sorted(groups)
    while len(picked) < count and any(groups.values()):
        for key in group_order:
            if groups[key] and len(picked) < count:
                picked.append(groups[key].pop())
    return picked


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=50, help="Total pilot size")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    eligible = find_eligible_bills()
    eligible_ids = {bill_id for bill_id, _, _ in eligible}

    required = [b for b in load_validation_bill_ids() if b in eligible_ids]
    skipped = set(load_validation_bill_ids()) - set(required)
    if skipped:
        print(f"Validation bills without a summary, skipped: {sorted(skipped)}")

    others = [b for b in eligible if b[0] not in set(required)]
    pilot = required + spread_sample(others, args.size - len(required), args.seed)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(" ".join(pilot) + "\n")
    print(f"Saved {len(pilot)} bill ids to {OUTPUT_FILE}")
    print(" ".join(pilot))
