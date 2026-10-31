import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from analysis.axis_analysis_client import AXIS_SCHEMA_VERSION
from analysis.bill_analysis_client import SCHEMA_VERSION
import db.db_utils as db_utils
from calc_institutional import calculate_institutional_scores
from pymongo import UpdateOne

from utils.stat_utils import within_group_zscores
from utils.vote_utils import build_bill_id, get_vote_value


DATA_DIR = Path("data")
MEMBER_VOTES_DIR = Path("data/organized_votes")
INPUT_COLLECTION = "bill_analyses"
OUTPUT_DIR = Path("data/legislator_profiles")
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT_COLLECTION = "legislator_profiles"
AXIS_INPUT_COLLECTION = "bill_axis_analyses"

# left_right is derived from category analyses, institutional is computed from roll calls
LEFT_RIGHT = "left_right"
INSTITUTIONAL = "institutional"

# Profile fields of {name: {score, bill_count}} that get weighted and ranked
RANKED_FIELDS = ("primary_categories", "axes")

PARTIES = {"D", "R"}
MIN_BILLS_FOR_Z = 10


def extract_top_votes(votes, n=3):
    """
    Return the top N conservative and top N liberal actions for a category,
    ranked strictly by their weighted impact on the legislator's final score.
    """
    # Sort strictly by weighted_score (Most negative to most positive)
    sorted_votes = sorted(votes, key=lambda v: v["weighted_score"])

    def clean(vote):
        return {
            "bill_id": vote["bill_id"],
            "vote": vote["vote"],
            "date": vote["date"],
            "score_impact": round(vote["weighted_score"], 3), 
        }

    # Filter out 0-impact or opposite-impact votes before slicing.
    # This prevents a moderately liberal vote from being labeled "Most Conservative" 
    # just because a member has literally zero conservative votes.
    conservative_actions = [v for v in sorted_votes if v["weighted_score"] > 0]
    liberal_actions = [v for v in sorted_votes if v["weighted_score"] < 0]

    return {
        # Grab the highest positive scores (end of the list, reversed)
        "conservative": [clean(v) for v in conservative_actions[-n:][::-1]],
        # Grab the lowest negative scores (start of the list)
        "liberal": [clean(v) for v in liberal_actions[:n]],
    }


def extract_recent_votes(all_votes_with_category, n=5, threshold=0.1):
    """
    Return the N most recent high-impact votes overall.
    Threshold uses weighted_score, so it filters out low-impact or highly-bipartisan votes.
    """
    conservative = sorted(
        [v for v in all_votes_with_category if v["weighted_score"] > threshold],
        key=lambda v: v.get("date", ""),
        reverse=True,
    )[:n]

    liberal = sorted(
        [v for v in all_votes_with_category if v["weighted_score"] < -threshold],
        key=lambda v: v.get("date", ""),
        reverse=True,
    )[:n]

    def clean(vote):
        return {
            "bill_id": vote["bill_id"],
            "vote": vote["vote"],
            "date": vote["date"],
            "score_impact": round(vote["weighted_score"], 3),
            "category": vote["category"],
        }

    return {
        "conservative": [clean(v) for v in conservative],
        "liberal": [clean(v) for v in liberal],
    }


def calculate_average_scores(vote_data_dict):
    """ "Helper function to calculate average scores for each category given a dict of votes per category."""
    results = {}

    for category, votes in vote_data_dict.items():
        if not votes:
            continue

        avg_score = round(sum(v["weighted_score"] for v in votes) / len(votes), 3)

        results[category] = {
            "score": avg_score,
            "bill_count": len(votes),
        }

    return results


def check_inputs(model, schema, congress, chamber, bill_type):
    if congress and congress < 113:
        raise ValueError(f"Congress must be at least 113, not {congress}")
    if chamber and chamber not in {"house", "senate"}:
        raise ValueError(f"Chamber must be (house, senate) not {chamber}")
    if bill_type:
        for bt in bill_type:
            if bt not in {"hr", "hjres", "s", "sjres"}:
                raise ValueError(f"Bill types must be (hr, hjres, s, sjres) not {bt}")


def collect_category_votes(
    political_categories, bill_id, vote, date, vote_value, primary_votes, sub_votes
):
    """
    Add one vote's weighted score to each category/subcategory the bill was tagged with.
    Returns the weighted scores added to primary categories (used to derive left_right).
    """
    primary_scores = []

    for primary_category in political_categories.get("primary_categories", []):
        if isinstance(primary_category, dict) and primary_category.get("name"):
            category_name = primary_category.get("name", "")
            partisan_score = primary_category.get("partisan_score", 0)
            impact_score = primary_category.get("impact_score", 0)
            weighted_score = partisan_score * impact_score * vote_value

            if partisan_score != 0:
                primary_votes[category_name].append(
                    {
                        "bill_id": bill_id,
                        "weighted_score": weighted_score,
                        "vote": vote,
                        "date": date,
                    }
                )
                primary_scores.append(weighted_score)

    for subcategory in political_categories.get("subcategories", []):
        if isinstance(subcategory, dict) and subcategory.get("name"):
            category_name = subcategory.get("name", "")
            partisan_score = subcategory.get("partisan_score", 0)
            impact_score = subcategory.get("impact_score", 0)
            weighted_score = partisan_score * impact_score * vote_value

            if partisan_score != 0:
                sub_votes[category_name].append(
                    {
                        "bill_id": bill_id,
                        "weighted_score": weighted_score,
                    }
                )

    return primary_scores


def collect_axis_votes(
    axis_analysis,
    primary_names,
    bill_id,
    vote_value,
    axis_votes,
    axis_category_votes,
):
    """
    Add one vote's weighted score to each axis the bill was scored on, and to that axis's
    breakdown for each primary category the bill is tagged with.
    """
    for axis_score in axis_analysis.get("axis_scores", []):
        position = axis_score.get("position", 0)
        relevance = axis_score.get("relevance", 0)
        if position == 0 or relevance == 0:
            continue

        axis = axis_score["axis"]
        record = {
            "bill_id": bill_id,
            "weighted_score": position * relevance * vote_value,
        }
        axis_votes[axis].append(record)
        for category_name in primary_names:
            axis_category_votes[axis][category_name].append(record)


def build_axis_results(axis_votes, axis_category_votes):
    """Average each axis, with its per-category breakdown."""
    results = calculate_average_scores(axis_votes)
    for axis, stats in results.items():
        stats["by_category"] = calculate_average_scores(axis_category_votes[axis])
    return results


def build_left_right_result(left_right_votes, primary_category_results):
    """
    left_right is derived from the category analyses: each bill's primary-category
    scores averaged together. Its per-category breakdown is the category scores themselves.
    """
    result = calculate_average_scores({LEFT_RIGHT: left_right_votes}).get(LEFT_RIGHT)
    if result is None:
        return None

    result["by_category"] = {
        category: {"score": stats["score"], "bill_count": stats["bill_count"]}
        for category, stats in primary_category_results.items()
    }
    return result


def calculate_legislator_ideology(legislator_votes, bill_analyses, axis_analyses=None):
    axis_analyses = axis_analyses or {}

    primary_category_votes = defaultdict(list)
    subcategory_votes = defaultdict(list)
    left_right_votes = []
    axis_votes = defaultdict(list)
    axis_category_votes = defaultdict(lambda: defaultdict(list))

    vote_count = 0
    for vote_record in legislator_votes:
        bill_id = build_bill_id(vote_record.get("bill", {}))
        if bill_id == "":
            continue
        vote = vote_record.get("vote", "").strip()
        date = vote_record.get("date", "")

        if bill_id not in bill_analyses:
            continue

        bill_analysis = bill_analyses[bill_id]

        vote_value = get_vote_value(vote)
        if vote_value == 0:
            continue

        political_categories = bill_analysis.get("political_categories", {})

        primary_scores = collect_category_votes(
            political_categories,
            bill_id,
            vote,
            date,
            vote_value,
            primary_category_votes,
            subcategory_votes,
        )
        if primary_scores:
            left_right_votes.append(
                {
                    "bill_id": bill_id,
                    "weighted_score": sum(primary_scores) / len(primary_scores),
                }
            )

        if bill_id in axis_analyses:
            primary_names = [
                c["name"]
                for c in political_categories.get("primary_categories", [])
                if isinstance(c, dict) and c.get("name")
            ]
            collect_axis_votes(
                axis_analyses[bill_id],
                primary_names,
                bill_id,
                vote_value,
                axis_votes,
                axis_category_votes,
            )

        vote_count += 1

    # Build primary category results with top_bills injected per category
    primary_category_results = calculate_average_scores(primary_category_votes)
    for category, votes in primary_category_votes.items():
        if category in primary_category_results:
            primary_category_results[category]["top_bills"] = extract_top_votes(votes)

    axis_results = build_axis_results(axis_votes, axis_category_votes)
    left_right = build_left_right_result(left_right_votes, primary_category_results)
    if left_right:
        axis_results[LEFT_RIGHT] = left_right

    # Flatten and deduplicate for overall recent votes
    all_votes_flat = [
        {**vote, "category": category}
        for category, votes in primary_category_votes.items()
        for vote in votes
    ]
    seen_bill_ids = set()
    all_votes_deduped = []
    for vote in sorted(all_votes_flat, key=lambda v: v.get("date", ""), reverse=True):
        if vote["bill_id"] not in seen_bill_ids:
            seen_bill_ids.add(vote["bill_id"])
            all_votes_deduped.append(vote)

    return {
        "primary_category_classifications": primary_category_results,
        "subcategory_classifications": calculate_average_scores(subcategory_votes),
        "axis_classifications": axis_results,
        "vote_count": vote_count,
        "recent_votes": extract_recent_votes(all_votes_deduped),
    }


def create_legislator_profile(
    legislator_info, legislator_votes, bill_analyses, axis_analyses=None
):
    """
    Create complete legislator ideology profile.

    Args:
        legislator_info (dict): Basic info {"member_id": "A000360", "name": "Rep. X", "party": "D", "state": "CA"}
        legislator_votes (list): Vote records
        bill_analyses (dict): Bill analysis results
        axis_analyses (dict): Bill axis analysis results, keyed by bill_id

    Returns:
        dict: Complete legislator profile with ideology scores
    """
    ideology_data = calculate_legislator_ideology(
        legislator_votes, bill_analyses, axis_analyses
    )

    profile = {
        "member_id": legislator_info.get("member_id"),
        "name": legislator_info.get("name"),
        "party": legislator_info.get("party"),
        "state": legislator_info.get("state"),
        "primary_categories": ideology_data["primary_category_classifications"],
        "axes": ideology_data["axis_classifications"],
        "vote_count": ideology_data["vote_count"],
        "recent_votes": ideology_data["recent_votes"],
    }

    return profile


def build_legislator_info(legislator_data):
    legislator_info = {
        "member_id": legislator_data.get("member_id"),
        "name": legislator_data.get("name"),
        "party": legislator_data.get("party"),
        "state": legislator_data.get("state"),
    }
    return legislator_info


def build_institutional_stats(institutional_scores, member_id):
    """Axis stats for the computed institutional axis, or None if the member has none."""
    stats = institutional_scores.get(member_id)
    if stats is None:
        return None
    return {
        "score": stats["score"],
        # Roll calls rather than bills, but named bill_count so every axis has the same shape
        "bill_count": stats["vote_count"],
        "defection_rate": stats["defection_rate"],
        "by_category": {},
    }


def process_all_legislators(
    bill_analyses,
    model,
    spec_hash,
    schema=None,
    chamber=None,
    axis_analyses=None,
    axis_schema=None,
    institutional_scores=None,
):
    """Process ideology scores for all legislators"""

    # If schema not specified, use latest version
    if schema is None:
        schema = SCHEMA_VERSION
    if axis_schema is None:
        axis_schema = AXIS_SCHEMA_VERSION
    institutional_scores = institutional_scores or {}

    profiles = []
    # Get collection
    members_with_votes_col = db_utils.get_collection("members_with_votes")

    for legislator_data in members_with_votes_col.find():
        # Senator's member_id looks like SXXX (Ex: S313), House uses bioguide which has 7 chars
        if chamber == "house":
            if len(legislator_data["member_id"]) <= 4:
                continue
        elif chamber == "senate":
            if len(legislator_data["member_id"]) > 4:
                continue
        legislator_info = build_legislator_info(legislator_data)
        # get legislator votes from member_votes collection
        legislator_votes = db_utils.get_collection("member_votes").find(
            {"member_id": legislator_data["member_id"]}
        )

        profile = create_legislator_profile(
            legislator_info, legislator_votes, bill_analyses, axis_analyses
        )

        # Don't add profiles for legislator's that had no votes with the active filters
        if profile["vote_count"] == 0:
            continue

        institutional = build_institutional_stats(
            institutional_scores, legislator_data["member_id"]
        )
        if institutional:
            profile["axes"][INSTITUTIONAL] = institutional

        # Add unique metadata
        profile["model"] = model
        profile["schema_version"] = schema
        profile["axis_schema_version"] = axis_schema
        profile["spec_hash"] = spec_hash

        profiles.append(profile)

    return profiles


def load_bill_analyses_from_data():
    """Load a dictionary of all bill analyses from data/ directory"""
    bill_analyses = {}

    for congress in DATA_DIR.iterdir():
        if not congress.is_dir():
            continue

        bills_dir = congress / "bills"
        if not bills_dir.exists():
            print(f"WARNING: No bills folder in {congress}")
            continue

        # Iterate through each bill type folder
        for bill_type_folder in bills_dir.iterdir():
            if not bill_type_folder.is_dir():
                continue

            # Ignore hres, sres, hconres, sconres
            # These are simple and concurrent resolutions that have no lawful power
            if bill_type_folder.name not in {"hr", "hjres", "s", "sjres"}:
                continue

            # Iterate through all individual bill folders
            for folder in bill_type_folder.iterdir():
                if not folder.is_dir():
                    continue

                # Check if bill was analyzed
                analysis_file = folder / "bill_analysis.json"
                if not analysis_file.exists():
                    continue

                with open(analysis_file, "r") as f:
                    analysis_data = json.load(f)

                # Congress + folder = id, ex. 119 + hr26 = hr25-119
                bill_id = folder.name + "-" + congress.name

                bill_analyses[bill_id] = analysis_data

    return bill_analyses


def get_spec_hash(
    model, schema, congress=None, chamber=None, bill_type=None, axis_schema=None
) -> str:
    """Generate a readable spec hash like: model_schema_axN_congress_chamber_billtype.

    Model and schema are required. Axis schema, congress, chamber, and bill_type are optional.
    """
    # If not specified, latest was used
    if schema is None:
        schema = SCHEMA_VERSION
    if axis_schema is None:
        axis_schema = AXIS_SCHEMA_VERSION

    parts = [model, str(schema), f"ax{axis_schema}"]
    if congress:
        parts.append(str(congress))
    else:
        parts.append("all")
    if chamber:
        parts.append(chamber.lower())
    else:
        parts.append("all")
    if bill_type:
        # sort for consistency (variance in inputting different bill_types)
        parts.append(",".join(sorted(bt.lower() for bt in bill_type)))
    else:
        parts.append("all")

    return "_".join(parts)


def load_analyses_from_db(collection_name, query, congress=None, bill_type=None):
    """Load analyses matching `query` from a collection, keyed by bill_id."""
    # Optional filters
    if congress:
        query["congress"] = str(congress)
    if bill_type:
        query["bill_type"] = {"$in": bill_type}

    analyses = {}
    for analysis_data in db_utils.get_collection(collection_name).find(query):
        bill_id = analysis_data.get("bill_id")
        if bill_id and bill_id not in analyses:
            analyses[bill_id] = analysis_data
    return analyses


def load_bill_analyses_from_db(
    model, schema_version=None, congress=None, bill_type=None
):
    """
    Load bill analyses from MongoDB, filtering by model and optionally schema version.
    """
    if schema_version is None:
        # Get latest schema version available
        schema_version = SCHEMA_VERSION

    query = {"model": model, "schema_version": schema_version}
    bill_analyses = load_analyses_from_db(INPUT_COLLECTION, query, congress, bill_type)

    print(
        f"Loaded {len(bill_analyses)} bill analyses for model '{model}' (schema v{schema_version})"
    )
    return bill_analyses


def load_axis_analyses_from_db(
    model, axis_schema_version=None, congress=None, bill_type=None
):
    """
    Load bill axis analyses from MongoDB, filtering by model and optionally axis schema version.
    """
    if axis_schema_version is None:
        axis_schema_version = AXIS_SCHEMA_VERSION

    query = {"model": model, "axis_schema_version": axis_schema_version}
    axis_analyses = load_analyses_from_db(
        AXIS_INPUT_COLLECTION, query, congress, bill_type
    )

    print(
        f"Loaded {len(axis_analyses)} axis analyses for model '{model}' (axis schema v{axis_schema_version})"
    )
    return axis_analyses


def apply_bayesian_weighting(profiles, field="primary_categories", skip=()):
    """
    Applies Bayesian weighting to legislator scores using their party's average as the prior.
    m is determined empirically per category based on the distribution of bill counts.
    `field` is the profile dict of {name: stats} to weight; names in `skip` are left as is.
    """
    # Collect all counts and raw data for prior calculation
    category_counts = defaultdict(list)
    party_totals = defaultdict(lambda: defaultdict(lambda: {"sum": 0, "count": 0}))

    # We use a threshold to ensure our "Party Average" (the Prior) is built from
    # legislators who actually have enough data to be representative
    MINIMUM_VOTE_FOR_PRIOR = 10

    for profile in profiles:
        party = profile.get("party")
        categories = profile.get(field, {})

        for cat_name, cat_data in categories.items():
            if cat_name in skip:
                continue
            count = cat_data.get("bill_count", 0)
            if count > 0:
                category_counts[cat_name].append(count)

            # Build party totals for the prior (C)
            if party in ["D", "R"] and count > MINIMUM_VOTE_FOR_PRIOR:
                party_totals[party][cat_name]["sum"] += cat_data["score"] * count
                party_totals[party][cat_name]["count"] += count

    # Compute the empirical 'm' per category (25th percentile)
    m_per_category = {}
    for cat_name, counts in category_counts.items():
        if counts:
            # We use the 25th percentile. If a category is sparse, m will be low.
            # If a category is dense, m will be higher to ensure stability.
            m_per_category[cat_name] = max(5, pd.Series(counts).quantile(0.25))
        else:
            m_per_category[cat_name] = 10  # Fallback

    # 3. Compute the party means (C)
    party_means = {}
    for party, cats in party_totals.items():
        party_means[party] = {}
        for cat_name, stats in cats.items():
            if stats["count"] > 0:
                party_means[party][cat_name] = stats["sum"] / stats["count"]
            else:
                party_means[party][cat_name] = 0

    # 4. Apply the Bayesian Formula
    for profile in profiles:
        party = profile.get("party")

        for cat_name, cat_data in profile.get(field, {}).items():
            if cat_name in skip:
                continue
            v = cat_data.get("bill_count", 0)
            R = cat_data.get("score", 0)
            m = m_per_category.get(cat_name, 10)

            # Get the prior (C)
            if party in party_means and cat_name in party_means[party]:
                C = party_means[party][cat_name]
            else:
                # Fallback to absolute center (0) for Independents/others
                C = 0

            # Bayesian Calculation: W = (v*R + m*C) / (v + m)
            weighted_score = (v / (v + m)) * R + (m / (v + m)) * C

            # Store data
            cat_data["raw_score"] = R
            cat_data["score"] = round(weighted_score, 3)
            cat_data["party_prior"] = round(C, 3)
            cat_data["empirical_m"] = round(m, 1)

    return profiles


def add_within_party_z(profiles):
    """
    Add `within_party_z` to every axis: the score's z-score among members of the same party
    (D or R) with enough bills on that axis. None where it can't be computed.
    """
    groups = defaultdict(list)
    for profile in profiles:
        party = profile.get("party")
        for axis, stats in profile.get("axes", {}).items():
            stats["within_party_z"] = None
            if party in PARTIES and stats["bill_count"] >= MIN_BILLS_FOR_Z:
                groups[(party, axis)].append(stats)

    for group in groups.values():
        z_scores = within_group_zscores([stats["score"] for stats in group])
        for stats, z in zip(group, z_scores):
            stats["within_party_z"] = None if z is None else round(z, 3)

    return profiles


def generate_rankings(profiles):
    """Generate rankings for each category and axis across all profiles."""
    all_rows = []
    all_current_rows = []

    # Get current legislators
    current_legislators = set()
    legislator_collection = db_utils.get_collection("legislators").find(
        {"current": True}
    )
    for legislator in legislator_collection:
        # Check latest term to determine if senator or house member
        if legislator["terms"][-1]["type"] == "sen":
            current_legislators.add(legislator.get("lis"))
        else:
            current_legislators.add(legislator.get("bioguide"))

    # Collect data from every profile
    for profile in profiles:
        is_current = profile["member_id"] in current_legislators
        for field in RANKED_FIELDS:
            data = profile.get(field, {})

            for category, details in data.items():

                all_rows.append(
                    {
                        "field": field,
                        "category": category,
                        "score": details["score"],
                        "party": profile.get("party"),
                        "member_id": profile.get("member_id"),
                        "name": profile.get("name"),
                    }
                )
                if is_current:
                    all_current_rows.append(
                        {
                            "field": field,
                            "category": category,
                            "score": details["score"],
                            "party": profile.get("party"),
                            "member_id": profile.get("member_id"),
                            "name": profile.get("name"),
                        }
                    )

    # No rankings generated
    if all_rows == [] or all_current_rows == []:
        print("Cant generate rankings, not enough data yet")
        return profiles

    # Create one big DataFrame
    df = pd.DataFrame(all_rows)
    current_df = pd.DataFrame(all_current_rows)

    # Find counts
    total_counts = df.groupby(["field", "category"]).size().rename("total_members")

    total_current_counts = (
        current_df.groupby(["field", "category"]).size().rename("total_current_members")
    )

    # Compute rankings *within each (field, category) group*
    df["rank"] = df.groupby(["field", "category"])["score"].rank(
        method="min", ascending=False
    )
    df["percentile_rank"] = df.groupby(["field", "category"])["score"].rank(
        pct=True, ascending=True
    )
    current_df["current_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(method="min", ascending=False)
    current_df["current_percentile_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(pct=True, ascending=True)

    # Now push results back into each profile
    for profile in profiles:
        is_current = profile["member_id"] in current_legislators
        for field in RANKED_FIELDS:
            sub = df[(df["field"] == field) & (df["member_id"] == profile["member_id"])]

            for _, row in sub.iterrows():
                cat = row["category"]
                # make sure the category exists
                if cat not in profile[field]:
                    profile[field][cat] = {}

                total_members = int(total_counts.get((field, cat), 0))
                total_current_members = int(total_current_counts.get((field, cat), 0))

                profile[field][cat].update(
                    {
                        "rank": int(row["rank"]),
                        "percentile_rank": round(row["percentile_rank"], 3),
                        # Add member count info (so we can contextualize rankings)
                        "total_members": total_members,
                        "total_current_members": total_current_members,
                    }
                )
                # Set to -1 (N/A), will be updated in following loop if current
                profile[field][cat].update(
                    {
                        "current_rank": -1,
                        "current_percentile_rank": -1,
                    }
                )

            # Have to iterate through current ranks separately (since its a different dataframe)
            if is_current:
                current_sub = current_df[
                    (current_df["field"] == field)
                    & (current_df["member_id"] == profile["member_id"])
                ]
                for _, current_row in current_sub.iterrows():
                    cat = current_row["category"]

                    profile[field][cat].update(
                        {
                            "current_rank": int(current_row["current_rank"]),
                            "current_percentile_rank": round(
                                current_row["current_percentile_rank"], 3
                            ),
                        }
                    )

    return profiles


def write_profiles_to_db(profiles):
    """Write legislator profiles to MongoDB collection."""
    count = 0
    actions = []
    for profile in profiles:
        query = {
            "member_id": profile["member_id"],
            "model": profile["model"],
            "schema_version": profile["schema_version"],
            "spec_hash": profile["spec_hash"],
        }
        actions.append(
            UpdateOne(
                query,
                {"$set": profile, "$currentDate": {"last_modified": True}},
                upsert=True,
            )
        )
        count += 1

    if actions:
        db_utils.bulk_write(OUTPUT_COLLECTION, actions)
    print(f"Updated profile for {count} members")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # REQUIRED ARGUMENT
    parser.add_argument(
        "--model",
        required=True,
        help="Specify the model of the analyses to use (e.g., 'gemini-2.5-flash-lite')",
    )
    parser.add_argument(
        "--schema",
        type=int,
        help="Optionally specify schema version (defaults to latest)",
    )
    parser.add_argument(
        "--axis_schema",
        type=int,
        help="Optionally specify axis schema version (defaults to latest)",
    )
    # Optional paramaters
    parser.add_argument(
        "--congress",
        type=int,
        help="Pass a specific congress to generate plots for (defaults to all data)",
    )
    parser.add_argument(
        "--chamber", help="Specify the chamber of the analyses to use (house, senate)"
    )
    parser.add_argument(
        "--bill_type", nargs="+", help="Specify the bill type(s) to use "
    )

    args = parser.parse_args()

    # Check inputs
    check_inputs(args.model, args.schema, args.congress, args.chamber, args.bill_type)

    # Load bill analyses for specific model
    bill_analyses = load_bill_analyses_from_db(
        args.model, args.schema, args.congress, args.bill_type
    )

    # Load axis analyses for the same model (axes are skipped if none exist yet)
    axis_analyses = load_axis_analyses_from_db(
        args.model, args.axis_schema, args.congress, args.bill_type
    )
    if not axis_analyses:
        print("WARNING: No axis analyses found, profiles will only have category scores")

    # Check if bill analyses is empty
    if bill_analyses:

        # Get hash
        spec_hash = get_spec_hash(
            args.model,
            args.schema,
            args.congress,
            args.chamber,
            args.bill_type,
            args.axis_schema,
        )

        # Institutional axis is computed from all roll calls, not just analyzed bills
        institutional_scores = calculate_institutional_scores(
            args.congress, args.chamber
        )

        # Process all legislators
        profiles = process_all_legislators(
            bill_analyses,
            args.model,
            spec_hash,
            args.schema,
            args.chamber,
            axis_analyses,
            args.axis_schema,
            institutional_scores,
        )

        # Apply Bayesian weighting (institutional is already computed over all roll calls)
        profiles = apply_bayesian_weighting(profiles)
        profiles = apply_bayesian_weighting(profiles, "axes", skip={INSTITUTIONAL})

        # Compare members to their own party
        profiles = add_within_party_z(profiles)

        # Create rankings for each category and axis
        profiles = generate_rankings(profiles)

        # Write profiles
        write_profiles_to_db(profiles)
    else:
        print(
            f"No analyses were found for model: {args.model} with schema version: {args.schema if args.schema else 'latest'}"
        )
