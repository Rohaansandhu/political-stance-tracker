"""
Validation report for the amendment pipeline. Run validate_axes.py on the amendment
spec_hashes as well; this covers the checks specific to amendments.

Checks (each prints a report and saves data/validation/<spec_hash>/amendments_*.csv):
  1. Coverage: amendment roll calls matched, analyzed, and substantive, per congress x chamber,
     plus per-member amendment vote counts
  2. Agreement: per-axis Spearman between passage-only and amendment-only member scores,
     overall and within each party
  3. Within-party spread: within-party SD per axis for passage-only, amendment-only, combined
  4. Amendment share: share of each member's combined axis records that are amendments,
     and the bills with the most scored amendment roll calls
  5. Pilot sanity: H.R. 2799 rolls 68-71 (Democrats voting YES should score more market-side)
  6. Tabling: how many tabling votes exist (excluded from profiles for now)

Usage:
    python3 src/validate_amendments.py --spec_hash gpt-6-luna_5_ax1_all_house_all
"""

import argparse
from collections import Counter

import pandas as pd

import db.db_utils as db_utils
from analysis.amendment_analysis_client import AMENDMENT_SCHEMA_VERSION
from calc_amendment_ideology import get_amendment_spec_hash
from get_amendments import BILL_TYPES
from utils.path_utils import get_output_dir

MIN_BILLS = 10
PARTIES = ("D", "R")
# Pilot: Lawler, Huizenga, Lucas, Wagner amendments to H.R. 2799 (all pro-market)
PILOT_VOTE_IDS = ("h68-118.2024", "h69-118.2024", "h70-118.2024", "h71-118.2024")
PILOT_AXIS = "economic"


def print_section(title):
    print(f"\n=== {title} ===")


def load_profiles(spec_hash):
    """{member_id: profile} for a spec_hash."""
    return {
        p["member_id"]: p
        for p in db_utils.get_collection("legislator_profiles").find({"spec_hash": spec_hash})
    }


def axis_frame(profiles, min_bills=MIN_BILLS):
    """Member x axis scores (only axes with enough bills), plus a party column."""
    rows = {}
    for member_id, profile in profiles.items():
        row = {
            axis: stats["score"]
            for axis, stats in profile.get("axes", {}).items()
            if stats.get("bill_count", 0) >= min_bills
        }
        row["party"] = profile.get("party")
        rows[member_id] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def check_coverage(model, chamber, amd_profiles, output_dir):
    print_section("1. Coverage")
    chamber_code = {"house": "h", "senate": "s"}.get(chamber)

    rollcall_query = {"category": "amendment", "bill.type": {"$in": sorted(BILL_TYPES)}}
    if chamber_code:
        rollcall_query["chamber"] = chamber_code
    rollcalls = Counter(
        (r["congress"], r["chamber"])
        for r in db_utils.get_collection("rollcall_votes").find(
            rollcall_query, {"congress": 1, "chamber": 1}
        )
    )

    matched, analyzed, substantive = Counter(), Counter(), Counter()
    analyses = {
        a["amendment_id"]: a["kind"]
        for a in db_utils.get_collection("amendment_analyses").find(
            {"model": model, "amendment_schema_version": AMENDMENT_SCHEMA_VERSION},
            {"amendment_id": 1, "kind": 1},
        )
    }
    amendment_query = {"chamber": chamber_code} if chamber_code else {}
    for amendment in db_utils.get_collection("amendments").find(amendment_query):
        key = (int(amendment["congress"]), amendment["chamber"])
        n_votes = sum(v["vote_direction"] == 1 for v in amendment["votes"])
        matched[key] += n_votes
        kind = analyses.get(amendment["amendment_id"])
        if kind:
            analyzed[key] += n_votes
            substantive[key] += n_votes if kind == "substantive" else 0

    rows = []
    for key in sorted(rollcalls):
        total = rollcalls[key]
        rows.append(
            {
                "congress": key[0],
                "chamber": key[1],
                "rollcalls": total,
                "matched": matched[key],
                "matched_pct": round(100 * matched[key] / total, 1),
                "analyzed": analyzed[key],
                "substantive": substantive[key],
                "substantive_pct_of_analyzed": round(100 * substantive[key] / analyzed[key], 1)
                if analyzed[key]
                else None,
            }
        )
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    df.to_csv(output_dir / "amendments_coverage.csv", index=False)

    counts = pd.Series({m: p["vote_count"] for m, p in amd_profiles.items()}, dtype=float)
    if len(counts):
        print(
            f"\nAmendment votes per member (amendment-only profiles, n={len(counts)}): "
            f"median {counts.median():.0f}, p10 {counts.quantile(0.1):.0f}, "
            f"p90 {counts.quantile(0.9):.0f}"
        )
        counts.rename("amendment_vote_count").to_csv(output_dir / "amendments_member_counts.csv")
    return df


def check_agreement(passage, amendments, output_dir):
    print_section("2. Agreement: passage-only vs amendment-only (Spearman)")
    axes = sorted((set(passage.columns) & set(amendments.columns)) - {"party"})
    rows = []
    for axis in axes:
        joined = pd.concat(
            [passage[axis].rename("passage"), amendments[axis].rename("amd"), passage["party"]],
            axis=1,
            join="inner",
        ).dropna()
        row = {"axis": axis, "n": len(joined)}
        row["all"] = joined["passage"].corr(joined["amd"], method="spearman") if len(joined) > 2 else None
        for party in PARTIES:
            group = joined[joined["party"] == party]
            row[party] = group["passage"].corr(group["amd"], method="spearman") if len(group) > 2 else None
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        print("No axes with enough bills in both profile sets")
        return df
    print(df.round(3).to_string(index=False))
    for row in rows:
        for key in ("all", *PARTIES):
            if row[key] is not None and row[key] < 0:
                print(f"RED FLAG: {row['axis']} agreement is negative ({key}: {row[key]:.3f})")
    df.to_csv(output_dir / "amendments_agreement.csv", index=False)
    return df


def check_spread(frames, output_dir):
    print_section("3. Within-party SD per axis")
    rows = []
    for name, frame in frames.items():
        for axis in sorted(set(frame.columns) - {"party"}):
            for party in PARTIES:
                values = frame.loc[frame["party"] == party, axis].dropna()
                if len(values) > 2:
                    rows.append(
                        {"axis": axis, "party": party, "set": name, "sd": values.std(), "n": len(values)}
                    )
    df = pd.DataFrame(rows)
    if df.empty:
        print("Not enough data")
        return df
    wide = df.pivot_table(index=["axis", "party"], columns="set", values="sd")
    print(wide.round(3).to_string())
    wide.to_csv(output_dir / "amendments_within_party_sd.csv")
    return wide


def check_share(amd_profiles, combined_profiles, model, output_dir):
    print_section("4. Amendment share in combined profiles")
    rows = []
    for member_id, combined in combined_profiles.items():
        amd_axes = (amd_profiles.get(member_id) or {}).get("axes", {})
        for axis, stats in combined.get("axes", {}).items():
            if axis == "institutional" or not stats.get("bill_count"):
                continue
            amd_count = amd_axes.get(axis, {}).get("bill_count", 0)
            rows.append(
                {
                    "member_id": member_id,
                    "party": combined.get("party"),
                    "axis": axis,
                    "combined_records": stats["bill_count"],
                    "amendment_records": amd_count,
                    "amendment_share": amd_count / stats["bill_count"],
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        summary = df.groupby("axis")["amendment_share"].describe(percentiles=[0.1, 0.5, 0.9])
        print(summary[["count", "10%", "50%", "90%", "max"]].round(3).to_string())
        df.to_csv(output_dir / "amendments_share.csv", index=False)

    substantive = {
        a["amendment_id"]
        for a in db_utils.get_collection("amendment_analyses").find(
            {"model": model, "amendment_schema_version": AMENDMENT_SCHEMA_VERSION, "kind": "substantive"},
            {"amendment_id": 1},
        )
    }
    per_bill = Counter()
    for amendment in db_utils.get_collection("amendments").find(
        {"amendment_id": {"$in": sorted(substantive)}}
    ):
        per_bill[amendment["amended_bill_id"]] += sum(v["vote_direction"] == 1 for v in amendment["votes"])
    top = pd.DataFrame(per_bill.most_common(10), columns=["bill_id", "scored_amendment_rollcalls"])
    print("\nTop bills by scored amendment roll calls:")
    print(top.to_string(index=False))
    top.to_csv(output_dir / "amendments_top_bills.csv", index=False)
    return df


def check_pilot(frames, output_dir):
    print_section(f"5. Pilot sanity: H.R. 2799 pro-market amendments, {PILOT_AXIS} axis")
    rollcalls = db_utils.get_collection("rollcall_votes")
    rows = []
    for vote_id in PILOT_VOTE_IDS:
        rollcall = rollcalls.find_one({"vote_id": vote_id})
        if not rollcall:
            continue
        dem_votes = {"yes": [], "no": []}
        for pos, members in rollcall["votes"].items():
            side = {"aye": "yes", "yea": "yes", "no": "no", "nay": "no"}.get(pos.lower())
            if side is None:
                continue
            dem_votes[side] += [m["id"] for m in members if m != "VP" and m.get("party") == "D"]

        for name, frame in frames.items():
            if PILOT_AXIS not in frame.columns:
                continue
            yes = frame[PILOT_AXIS].reindex(dem_votes["yes"]).dropna()
            no = frame[PILOT_AXIS].reindex(dem_votes["no"]).dropna()
            if not len(yes) and not len(no):
                continue  # None of the voters are in this spec (e.g. a Senate spec)
            rows.append(
                {
                    "vote_id": vote_id,
                    "set": name,
                    "dem_yes": len(dem_votes["yes"]),
                    "dem_no": len(dem_votes["no"]),
                    "yes_mean": yes.mean() if len(yes) else None,
                    "no_mean": no.mean() if len(no) else None,
                    "pass": bool(len(yes) and len(no) and yes.mean() > no.mean()),
                }
            )
    df = pd.DataFrame(rows)
    print(
        df.round(3).to_string(index=False)
        if not df.empty
        else "n/a: none of the pilot roll calls' voters are in these profiles (House votes)"
    )
    df.to_csv(output_dir / "amendments_pilot.csv", index=False)
    return df


def check_tabling():
    print_section("6. Tabling votes")
    n = db_utils.get_collection("amendments").count_documents({"votes.vote_direction": -1})
    print(
        f"{n} amendments have a tabling vote in the amendments collection. Tabling motions are "
        "categorized 'procedural' in rollcall_votes, so get_amendments.py doesn't load them yet."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--spec_hash", required=True, help="Passage-only spec_hash, e.g. gpt-6-luna_5_ax1_all_house_all"
    )
    parser.add_argument("--amendment_schema", type=int, default=AMENDMENT_SCHEMA_VERSION)
    args = parser.parse_args()

    passage_profiles = load_profiles(args.spec_hash)
    if not passage_profiles:
        raise SystemExit(f"No profiles for {args.spec_hash}")
    any_profile = next(iter(passage_profiles.values()))
    model_name = any_profile["model"]
    chamber_name = "house" if "_house_" in f"{args.spec_hash}_" else (
        "senate" if "_senate_" in f"{args.spec_hash}_" else None
    )

    amd_spec = get_amendment_spec_hash(args.spec_hash, args.amendment_schema, "amendments")
    combined_spec = get_amendment_spec_hash(args.spec_hash, args.amendment_schema, "combined")
    amendment_profiles = load_profiles(amd_spec)
    combined_profiles = load_profiles(combined_spec)
    print(
        f"Profiles: passage {len(passage_profiles)} ({args.spec_hash}), "
        f"amendments {len(amendment_profiles)} ({amd_spec}), "
        f"combined {len(combined_profiles)} ({combined_spec})"
    )

    out_dir = get_output_dir("data/validation", args.spec_hash)
    check_coverage(model_name, chamber_name, amendment_profiles, out_dir)

    profile_frames = {
        "passage": axis_frame(passage_profiles),
        "amendments": axis_frame(amendment_profiles),
        "combined": axis_frame(combined_profiles),
    }
    check_agreement(profile_frames["passage"], profile_frames["amendments"], out_dir)
    check_spread(profile_frames, out_dir)
    check_share(amendment_profiles, combined_profiles, model_name, out_dir)
    # Pilot uses every member with a score, since amendment-only counts are small
    check_pilot(
        {name: axis_frame(p, min_bills=1) for name, p in
         (("passage", passage_profiles), ("amendments", amendment_profiles), ("combined", combined_profiles))},
        out_dir,
    )
    check_tabling()
    print(f"\nCSVs written to {out_dir}")
