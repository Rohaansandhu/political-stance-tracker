"""
Validation gate for the ideological axes. Run before building anything on top of them.

Checks (each prints a report and saves a CSV to data/validation/<spec_hash>/):
  1. Axis correlation matrix across members; flags near-duplicate axes
     (also computed within each party, since party sorting inflates the all-member figures)
  2. PCA on the member x axis matrix; how much variance beyond the party divide
  3. Known cross-party votes land where expected
  4. Inter-model agreement on bill-level axis positions (needs --compare_models)
  5. Which categories each axis actually appears in vs. the applicability matrix
"""

import argparse
import json
import re
from collections import defaultdict

import numpy as np
import pandas as pd

import db.db_utils as db_utils
from analysis.definitions import DEFINITIONS_DIR, load_axes
from utils.path_utils import get_output_dir

# Members need this many scored bills on an axis for it to count
MIN_BILLS = 10
MIN_MEMBERS = 20
# Axes correlating above this are measuring the same thing
CORRELATION_FLAG = 0.9
# Parties compared separately in the within-party correlation check
PARTIES = ("D", "R")
# An axis "engages" a bill at or above this relevance
RELEVANCE_THRESHOLD = 0.3
# Models agreeing less than this (Pearson r) on an axis means it needs redefining
AGREEMENT_FLAG = 0.5
# Share of a category's bills that must engage an axis to count as "present"
APPLICABILITY_SHARE = 0.2


def load_profile_matrix(spec_hash):
    """Member x axis score matrix from legislator_profiles, plus party and spec metadata."""
    profiles = list(
        db_utils.get_collection("legislator_profiles").find({"spec_hash": spec_hash})
    )
    if not profiles:
        raise ValueError(f"No profiles found for spec_hash {spec_hash}")

    rows, parties = {}, {}
    for profile in profiles:
        rows[profile["member_id"]] = {
            axis: stats["score"]
            for axis, stats in profile.get("axes", {}).items()
            if stats["bill_count"] >= MIN_BILLS
        }
        parties[profile["member_id"]] = profile["party"]

    meta = {
        key: profiles[0][key] for key in ("model", "schema_version", "axis_schema_version")
    }
    return pd.DataFrame.from_dict(rows, orient="index"), pd.Series(parties), meta


def print_section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def flag_correlated_pairs(corr, label):
    """Print every axis pair correlating above CORRELATION_FLAG. Returns the flagged pairs."""
    axes = list(corr.columns)
    flagged = [
        (a, b, corr.loc[a, b])
        for i, a in enumerate(axes)
        for b in axes[i + 1 :]
        if abs(corr.loc[a, b]) > CORRELATION_FLAG
    ]
    for a, b, r in flagged:
        print(f"FLAG ({label}): {a} and {b} correlate at {r:.2f}; merge or redefine")
    if not flagged:
        print(f"OK ({label}): no axis pair correlates above {CORRELATION_FLAG}")
    return flagged


def check_correlations(matrix, parties, output_dir):
    print_section("1. Axis correlation across members")
    corr = matrix.corr(min_periods=MIN_MEMBERS)
    print(corr.to_string(float_format="%.2f"))
    corr.to_csv(output_dir / "axis_correlations.csv")
    flag_correlated_pairs(corr, "all members")
    print(
        "\nNote: members vote along party lines, so any axis whose bills split by party "
        "correlates with the others across the whole chamber. The within-party check below "
        "is the better test of redundancy."
    )

    # Within a party the party-sorting effect is gone, leaving real overlap between axes
    print("\n1b. Axis correlation within each party")
    for party in PARTIES:
        in_party = matrix[parties.reindex(matrix.index) == party]
        if len(in_party) < MIN_MEMBERS:
            print(f"{party}: only {len(in_party)} members, skipping")
            continue

        party_corr = in_party.corr(min_periods=MIN_MEMBERS)
        party_corr.to_csv(output_dir / f"axis_correlations_{party}.csv")
        print(f"\n{party} only (n={len(in_party)}): correlation with left_right")
        if "left_right" in party_corr:
            print(
                party_corr["left_right"]
                .drop("left_right")
                .to_frame("r")
                .T.to_string(float_format="%.2f")
            )
        flag_correlated_pairs(party_corr, f"{party} only")


def check_pca(matrix, parties, output_dir):
    print_section("2. PCA on member x axis matrix")
    complete = matrix.dropna()
    complete = complete.loc[:, complete.std() > 0]
    if len(complete) <= len(complete.columns):
        print("Not enough members with every axis scored; skipping")
        return

    standardized = (complete - complete.mean()) / complete.std()
    u, s, vt = np.linalg.svd(standardized.to_numpy(), full_matrices=False)
    explained = s**2 / np.sum(s**2)

    for i, share in enumerate(explained, start=1):
        print(f"PC{i}: {share:6.1%} of variance")
    print(f"Variance beyond PC1: {1 - explained[0]:.1%}")

    in_party = parties.loc[complete.index].isin(["D", "R"])
    pc1 = u[:, 0] * s[0]
    party_flag = (parties.loc[complete.index] == "R").astype(float)
    r = np.corrcoef(pc1[in_party.to_numpy()], party_flag[in_party].to_numpy())[0, 1]
    print(f"|corr(PC1, party)| = {abs(r):.2f} (expect a large first component = party)")

    loadings = pd.DataFrame(
        vt.T[:, :3], index=complete.columns, columns=["PC1", "PC2", "PC3"]
    )
    print("\nLoadings:")
    print(loadings.to_string(float_format="%.2f"))
    loadings.to_csv(output_dir / "pca_loadings.csv")


def parse_bill_id(bill_id):
    """'hr7888-118' -> {'type': 'hr', 'number': 7888, 'congress': 118}"""
    match = re.fullmatch(r"([a-z]+)(\d+)-(\d+)", bill_id)
    return {"type": match[1], "number": int(match[2]), "congress": int(match[3])}


def party_vote_tally(bill_id, party_by_member):
    """Yea/Nay counts per party across all recorded member votes on a bill."""
    bill = parse_bill_id(bill_id)
    query = {f"bill.{key}": value for key, value in bill.items()}

    tally = defaultdict(lambda: {"yea": 0, "nay": 0})
    for vote in db_utils.get_collection("member_votes").find(query):
        party = party_by_member.get(vote["member_id"])
        value = vote["vote"].lower()
        if value in {"yea", "aye"}:
            tally[party]["yea"] += 1
        elif value in {"nay", "no"}:
            tally[party]["nay"] += 1
    return {party: counts for party, counts in tally.items() if party in {"D", "R"}}


def format_tally(tally):
    return ", ".join(f"{p}: {c['yea']}Y/{c['nay']}N" for p, c in sorted(tally.items()))


def check_known_votes(meta, output_dir):
    print_section("3. Known cross-party votes")
    with open(DEFINITIONS_DIR / "validation_bills.json") as f:
        cases = json.load(f)["cases"]

    party_by_member = {
        m["member_id"]: m["party"]
        for m in db_utils.get_collection("members_with_votes").find()
    }
    axis_analyses = db_utils.get_collection("bill_axis_analyses")

    rows = []
    for case in cases:
        doc = axis_analyses.find_one(
            {
                "bill_id": case["bill_id"],
                "model": meta["model"],
                "axis_schema_version": meta["axis_schema_version"],
            }
        )
        scores = {s["axis"]: s for s in (doc or {}).get("axis_scores", [])}
        tally = format_tally(party_vote_tally(case["bill_id"], party_by_member))

        for axis, expected in case["expected"].items():
            score = scores.get(axis)
            if doc is None:
                status, position = "no analysis", None
            elif score is None:
                status, position = "axis omitted", None
            else:
                position = score["position"]
                status = "pass" if position * expected > 0 else "FAIL"
            rows.append(
                {
                    "case": case["label"],
                    "bill_id": case["bill_id"],
                    "axis": axis,
                    "expected": expected,
                    "position": position,
                    "status": status,
                    "party_votes": tally,
                }
            )

    results = pd.DataFrame(rows)
    print(results.to_string(index=False, float_format="%.2f"))
    results.to_csv(output_dir / "known_votes.csv", index=False)
    print(f"\n{(results['status'] == 'pass').sum()}/{len(results)} expectations met")


def load_positions(model, axis_schema):
    """Every (bill, axis) position for a model as a DataFrame."""
    rows = [
        {"bill_id": doc["bill_id"], "axis": s["axis"], **s}
        for doc in db_utils.get_collection("bill_axis_analyses").find(
            {"model": model, "axis_schema_version": axis_schema}
        )
        for s in doc["axis_scores"]
    ]
    return pd.DataFrame(rows, columns=["bill_id", "axis", "position", "relevance"])


def check_model_agreement(model_a, model_b, axis_schema, output_dir):
    print_section(f"4. Inter-model agreement: {model_a} vs {model_b}")
    merged = load_positions(model_a, axis_schema).merge(
        load_positions(model_b, axis_schema),
        on=["bill_id", "axis"],
        suffixes=("_a", "_b"),
    )
    engaged = merged[
        (merged["relevance_a"] >= RELEVANCE_THRESHOLD)
        & (merged["relevance_b"] >= RELEVANCE_THRESHOLD)
    ]

    rows = []
    for axis, group in engaged.groupby("axis"):
        r = group["position_a"].corr(group["position_b"]) if len(group) >= 3 else np.nan
        rows.append(
            {
                "axis": axis,
                "bills": len(group),
                "pearson_r": r,
                "mean_abs_diff": (group["position_a"] - group["position_b"]).abs().mean(),
            }
        )

    results = pd.DataFrame(rows)
    print(results.to_string(index=False, float_format="%.2f"))
    results.to_csv(output_dir / "model_agreement.csv", index=False)

    for _, row in results.iterrows():
        if not row["pearson_r"] >= AGREEMENT_FLAG:
            print(f"FLAG: {row['axis']} agreement is low or unmeasurable; redefine")


def check_applicability(meta, output_dir):
    print_section("5. Axis x category applicability (observed vs. matrix)")
    category_docs = db_utils.get_collection("bill_analyses").find(
        {"model": meta["model"], "schema_version": meta["schema_version"]},
        {"bill_id": 1, "political_categories.primary_categories.name": 1},
    )
    categories_by_bill = {
        doc["bill_id"]: [
            c["name"] for c in doc["political_categories"]["primary_categories"]
        ]
        for doc in category_docs
    }

    bills_per_category = defaultdict(int)
    engaged = defaultdict(int)
    for doc in db_utils.get_collection("bill_axis_analyses").find(
        {"model": meta["model"], "axis_schema_version": meta["axis_schema_version"]}
    ):
        categories = categories_by_bill.get(doc["bill_id"], [])
        for category in categories:
            bills_per_category[category] += 1
        for score in doc["axis_scores"]:
            if score["relevance"] >= RELEVANCE_THRESHOLD:
                for category in categories:
                    engaged[(category, score["axis"])] += 1

    axes = [a for a in load_axes() if a["source"] == "llm"]
    share = pd.DataFrame(
        {
            a["id"]: {
                c: engaged[(c, a["id"])] / n for c, n in bills_per_category.items()
            }
            for a in axes
        }
    )
    print(share.to_string(float_format="%.2f"))
    share.to_csv(output_dir / "applicability.csv")

    for axis in axes:
        typical = axis["typical_categories"]
        listed = set(typical["most"]) | set(typical["some"])
        for category in share.index:
            observed = share.loc[category, axis["id"]]
            if observed >= APPLICABILITY_SHARE and category not in listed:
                print(f"UNLISTED: {axis['id']} appears in {category} ({observed:.0%} of bills)")
            elif observed < APPLICABILITY_SHARE and category in typical["most"]:
                print(f"RARE: {axis['id']} is rare in {category} ({observed:.0%}) but listed as typical")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        required=True,
        help="REQUIRED: Specify the hash of the profiles you want to validate",
    )
    parser.add_argument(
        "--compare_models",
        nargs=2,
        metavar=("MODEL_A", "MODEL_B"),
        help="Also compare bill-level axis positions between two models",
    )
    args = parser.parse_args()

    matrix, parties, meta = load_profile_matrix(args.spec_hash)
    output_dir = get_output_dir("data/validation", args.spec_hash)
    print(f"Validating {args.spec_hash}: {len(matrix)} members, axes {list(matrix.columns)}")

    check_correlations(matrix, parties, output_dir)
    check_pca(matrix, parties, output_dir)
    check_known_votes(meta, output_dir)
    if args.compare_models:
        check_model_agreement(*args.compare_models, meta["axis_schema_version"], output_dir)
    check_applicability(meta, output_dir)
