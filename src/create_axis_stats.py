"""
Precompute what the website's axis compass and fingerprint need, so the browser doesn't:

  - `current` and `axes.<id>.chamber_z` on every legislator profile, in place
  - an `axis_stats` document per (spec_hash, current): per-axis mean/SD and the
    axis-pair correlation matrices

Rerun after calc_member_ideology.py (which replaces `axes`, dropping `chamber_z`) and after
the `legislators` collection's `current` flags change.
"""

import argparse

import numpy as np
from pymongo import UpdateOne

import db.db_utils as db_utils
from analysis.definitions import load_axes
from schema.axis_stats import AxisStatsDoc
from utils.stat_utils import pearson_r, z_score

# Members need this many bills on an axis to count toward its stats (same rule as the
# histograms and validate_axes.py). chamber_z is still stored for everyone with the axis.
MIN_BILLS = 10
PARTIES = ("D", "R", "I")
# correlation scale -> profile field it correlates
CORRELATION_FIELDS = {"raw": "score", "party": "within_party_z"}

STATS_DECIMALS = 5
Z_DECIMALS = 3
R_DECIMALS = 3


def find_axis_spec_hashes():
    """spec_hashes of profile sets that have axes (v5 and later)."""
    return db_utils.get_collection("legislator_profiles").distinct(
        "spec_hash", {"axes": {"$exists": True}}
    )


def load_profiles(spec_hash, axis_ids):
    # `axes` is keyed by axis id, so each field is projected per axis
    fields = ("score", "within_party_z", "bill_count")
    projection = {"member_id": 1, "party": 1}
    projection.update({f"axes.{a}.{f}": 1 for a in axis_ids for f in fields})
    return list(
        db_utils.get_collection("legislator_profiles").find(
            {"spec_hash": spec_hash}, projection
        )
    )


def axis_entry(profile, axis):
    """The member's entry on an axis if it has enough bills to count, else None."""
    entry = profile.get("axes", {}).get(axis)
    if entry is None or entry["bill_count"] < MIN_BILLS:
        return None
    return entry


def rounded(value, decimals):
    return None if value is None else round(float(value), decimals)


def summarize(scores):
    """n, mean, population SD, min and max of scores. SD is None if it can't scale scores."""
    if not scores:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}

    arr = np.array(scores, dtype=float)
    sd = arr.std()
    return {
        "n": len(arr),
        "mean": rounded(arr.mean(), STATS_DECIMALS),
        "sd": rounded(sd, STATS_DECIMALS) if len(arr) >= 2 and sd > 0 else None,
        "min": rounded(arr.min(), STATS_DECIMALS),
        "max": rounded(arr.max(), STATS_DECIMALS),
    }


def axis_summary(profiles, axis):
    entries = [(p["party"], axis_entry(p, axis)) for p in profiles]
    entries = [(party, e) for party, e in entries if e is not None]

    summary = summarize([e["score"] for _, e in entries])
    summary["by_party"] = {
        party: {
            k: v
            for k, v in summarize([e["score"] for p, e in entries if p == party]).items()
            if k in ("n", "mean", "sd")
        }
        for party in PARTIES
    }
    return summary


def correlation_matrix(profiles, axes, field):
    """axis -> other axis -> {r, n} over members with a value for `field` on both axes."""
    matrix = {}
    for a in axes:
        matrix[a] = {}
        for b in axes:
            if a == b:
                continue
            pairs = []
            for p in profiles:
                ea, eb = axis_entry(p, a), axis_entry(p, b)
                if ea and eb and ea[field] is not None and eb[field] is not None:
                    pairs.append((ea[field], eb[field]))
            xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
            matrix[a][b] = {
                "r": rounded(pearson_r(xs, ys), R_DECIMALS),
                "n": len(pairs),
            }
    return matrix


def build_stats_doc(spec_hash, profiles, axes, current):
    doc = {
        "spec_hash": spec_hash,
        "current": current,
        "member_count": len(profiles),
        "min_bills": MIN_BILLS,
        "axes": {axis: axis_summary(profiles, axis) for axis in axes},
        "correlations": {
            scale: correlation_matrix(profiles, axes, field)
            for scale, field in CORRELATION_FIELDS.items()
        },
    }
    # Fail loudly if the document drifts from its schema
    AxisStatsDoc.model_validate(doc)
    return doc


def build_profile_updates(profiles, axes, current_ids, current_stats):
    """In-place updates: the `current` flag and each axis's chamber_z against current members."""
    actions = []
    for profile in profiles:
        fields = {"current": profile["member_id"] in current_ids}
        for axis, entry in profile.get("axes", {}).items():
            stats = current_stats["axes"].get(axis)
            z = z_score(entry["score"], stats["mean"], stats["sd"]) if stats else None
            fields[f"axes.{axis}.chamber_z"] = rounded(z, Z_DECIMALS)

        actions.append(
            UpdateOne(
                {"_id": profile["_id"]},
                {"$set": fields, "$currentDate": {"last_modified": True}},
            )
        )
    return actions


def process_spec_hash(spec_hash, current_ids, axis_ids):
    profiles = load_profiles(spec_hash, axis_ids)
    axes = [a for a in axis_ids if any(a in p.get("axes", {}) for p in profiles)]

    current_profiles = [p for p in profiles if p["member_id"] in current_ids]
    stats_docs = [
        build_stats_doc(spec_hash, current_profiles, axes, current=True),
        build_stats_doc(spec_hash, profiles, axes, current=False),
    ]

    # chamber_z is measured against current members, matching the website's compass
    profile_updates = build_profile_updates(profiles, axes, current_ids, stats_docs[0])

    db_utils.bulk_write("legislator_profiles", profile_updates)
    db_utils.bulk_write(
        "axis_stats",
        [
            UpdateOne(
                {"spec_hash": d["spec_hash"], "current": d["current"]},
                {"$set": d, "$currentDate": {"last_modified": True}},
                upsert=True,
            )
            for d in stats_docs
        ],
    )
    print(
        f"{spec_hash}: updated {len(profile_updates)} profiles "
        f"({len(current_profiles)} current), wrote {len(stats_docs)} axis_stats docs"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        help="Only this profile set (default: every set that has axes)",
    )
    args = parser.parse_args()

    current_ids = set(db_utils.get_current_member_ids())
    axis_ids = [a["id"] for a in load_axes()]
    spec_hashes = [args.spec_hash] if args.spec_hash else find_axis_spec_hashes()

    print(f"Processing {len(spec_hashes)} spec_hashes ({len(current_ids)} current members)")
    for spec_hash in spec_hashes:
        process_spec_hash(spec_hash, current_ids, axis_ids)
