import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from analysis.bill_analysis_client import SCHEMA_VERSION
import db.db_utils as db_utils
from pymongo import UpdateOne

from schema.legislator_profiles import LegislatorProfile, CategoryStats


DATA_DIR = Path("data")
MEMBER_VOTES_DIR = Path("data/organized_votes")
INPUT_COLLECTION = "bill_analyses"
OUTPUT_DIR = Path("data/legislator_profiles")
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT_COLLECTION = "legislator_profiles"


def build_bill_id(bill: dict) -> str:
    """
    Turn a bill object into a string for id.
    Example: {"congress":119,"number":242,"type":"hres"}
             -> "hres242-119"
    """
    if bill == {}:
        return ""
    congress = bill["congress"]
    number = bill["number"]
    btype = bill["type"]
    return "%s%s-%s" % (btype, number, congress)


def calculate_average_scores(vote_data_dict):
    """
    Helper to calculate average weighted scores from vote data.
    Returns a dictionary in the format:
    {
        "Category Name": {
            "score": float,
            "bills": [list of unique bill_ids],
            "bill_count": int
        },
        ...
    }
    """
    results = {}

    for category, votes in vote_data_dict.items():
        if not votes:
            continue

        # Compute the average weighted score
        avg_score = round(sum(v["weighted_score"] for v in votes) / len(votes), 3)

        # Collect bill IDs
        bill_ids = [v["bill_id"] for v in votes]

        # Build the result
        results[category] = {
            "score": avg_score,
            "bills": bill_ids,
            "bill_count": len(bill_ids),
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


def calculate_legislator_ideology(legislator_votes, bill_analyses):
    """
    Calculate ideology scores for a legislator based on their voting pattern.
    """

    # Store raw voting data for each spectrum/category
    # spectrum_votes = defaultdict(list)
    primary_category_votes = defaultdict(list)
    subcategory_votes = defaultdict(list)

    # Determined by the number of bills analyzed
    vote_count = 0
    for vote_record in legislator_votes:
        bill_id = build_bill_id(vote_record.get("bill", {}))
        if bill_id == "":
            continue
        vote = vote_record.get("vote", "").strip()

        # Skip if no analysis available for this bill
        if bill_id not in bill_analyses:
            continue

        bill_analysis = bill_analyses[bill_id]

        # Determine vote direction (1 for support, -1 for oppose)
        vote_value = get_vote_value(vote)
        # If they didn't vote, move on
        if vote_value == 0:
            continue

        # Process spectrums
        # for spectrum_name, spectrum_data in bill_analysis.get(
        #     "political_spectrums", {}
        # ).items():
        #     partisan_score = spectrum_data.get("partisan_score", 0)
        #     impact_score = spectrum_data.get("impact_score", 0)

        #     if partisan_score != 0:
        #         spectrum_votes[spectrum_name].append(
        #             {
        #                 "bill_id": bill_id,
        #                 "vote": vote,
        #                 "vote_value": vote_value,
        #                 "key_provisions": key_provisions,
        #                 "partisan_score": partisan_score,
        #                 "impact_score": impact_score,
        #                 "weighted_score": partisan_score * impact_score * vote_value,
        #             }
        #         )

        # Process categories
        political_categories = bill_analysis.get("political_categories", {})

        # Primary categories
        primary_categories = political_categories.get("primary_categories", [])
        for primary_category in primary_categories:
            if isinstance(primary_category, dict) and primary_category.get("name"):
                category_name = primary_category.get("name", "")
                partisan_score = primary_category.get("partisan_score", 0)
                impact_score = primary_category.get("impact_score", 0)
                weighted_score = partisan_score * impact_score * vote_value

                # Ignore bills that have no partisan relevance
                if partisan_score != 0:
                    vote_data = {
                        "bill_id": bill_id,
                        "weighted_score": weighted_score,
                    }

                    primary_category_votes[category_name].append(vote_data)

        # Subcategories
        for subcategory in political_categories.get("subcategories", []):
            if isinstance(subcategory, dict) and subcategory.get("name"):
                category_name = subcategory.get("name", "")
                partisan_score = subcategory.get("partisan_score", 0)
                impact_score = subcategory.get("impact_score", 0)
                weighted_score = partisan_score * impact_score * vote_value

                if partisan_score != 0:
                    subcategory_votes[category_name].append(
                        {
                            "bill_id": bill_id,
                            "weighted_score": weighted_score,
                        }
                    )
        vote_count += 1

    return {
        # "spectrum_scores": calculate_average_scores(spectrum_votes),
        "primary_category_classifications": calculate_average_scores(
            primary_category_votes
        ),
        "subcategory_classifications": calculate_average_scores(subcategory_votes),
        "vote_count": vote_count,
    }


def get_vote_value(vote):
    """Convert vote string to multiplier."""
    vote_lower = vote.lower().strip()

    # Support votes
    if vote_lower in {"yea", "yes", "aye", "y"}:
        return 1

    # Opposition votes
    if vote_lower in {"nay", "no", "n"}:
        return -1

    # Didn't vote
    return 0


def create_legislator_profile(legislator_info, legislator_votes, bill_analyses):
    """
    Create complete legislator ideology profile.

    Args:
        legislator_info (dict): Basic info {"member_id": "A000360", "name": "Rep. X", "party": "D", "state": "CA"}
        legislator_votes (list): Vote records
        bill_analyses (dict): Bill analysis results

    Returns:
        dict: Complete legislator profile with ideology scores
    """

    ideology_data = calculate_legislator_ideology(legislator_votes, bill_analyses)

    # Create standardized spectrum scores (map to common left-right, authoritarian-libertarian)
    # standard_scores = standardize_spectrum_scores(ideology_data["spectrum_scores"])

    profile = {
        "member_id": legislator_info.get("member_id"),
        "name": legislator_info.get("name"),
        "party": legislator_info.get("party"),
        "state": legislator_info.get("state"),
        "primary_categories": ideology_data["primary_category_classifications"],
        "subcategories": ideology_data["subcategory_classifications"],
        # "detailed_spectrums": ideology_data["spectrum_scores"],
        "vote_count": ideology_data["vote_count"],
    }

    return profile


def standardize_spectrum_scores(spectrum_scores):
    """Convert various spectrum scores to standardized left_right and authoritarian_libertarian scales."""
    standard_scores = {}

    # Map various spectrums to left-right scale
    left_right_spectrums = [
        "Government Role",
        "Economic Policy",
        "Social Policy",
        "Environmental Spectrum",
        "Cultural Policy",
        "Economic Globalization",
        "Corporate Power",
        "Progress vs Tradition",
        "Foreign Policy",
        "Tech & Privacy",
        "Individualism vs Collectivism",
        "Criminal Justice",
        "Education",
        "Immigration",
    ]
    left_right_values = []

    for spectrum in left_right_spectrums:
        if spectrum in spectrum_scores:
            left_right_values.append(spectrum_scores[spectrum])

    if left_right_values:
        standard_scores["left_right"] = round(
            sum(left_right_values) / len(left_right_values), 3
        )

    # Map to authoritarian-libertarian scale
    # -1 = libertarian, 1 = authoritarian
    auth_lib_spectrums = [
        "Federalism",
        "Civil Liberties vs Security",
        "Democracy vs Authoritarianism",
        # "Populism vs Elitism", Ambigous spectrum so leaving it out for now
    ]
    auth_lib_values = []

    for spectrum in auth_lib_spectrums:
        if spectrum in spectrum_scores:
            auth_lib_values.append(spectrum_scores[spectrum])

    if auth_lib_values:
        standard_scores["authoritarian_libertarian"] = round(
            sum(auth_lib_values) / len(auth_lib_values), 3
        )

    return standard_scores


def build_legislator_info(legislator_data):
    legislator_info = {
        "member_id": legislator_data.get("member_id"),
        "name": legislator_data.get("name"),
        "party": legislator_data.get("party"),
        "state": legislator_data.get("state"),
    }
    return legislator_info


def process_all_legislators(bill_analyses, model, spec_hash, schema=None, chamber=None):
    """Process ideology scores for all legislators"""

    # If schema not specified, use latest version
    if schema is None:
        schema = SCHEMA_VERSION

    profiles = []
    # Get collection
    member_votes_col = db_utils.get_collection("member_votes")

    for legislator_data in member_votes_col.find():
        # Senator's member_id looks like SXXX (Ex: S313), House uses bioguide which has 7 chars
        if chamber == "house":
            if len(legislator_data["member_id"]) <= 4:
                continue
        elif chamber == "senate":
            if len(legislator_data["member_id"]) > 4:
                continue
        legislator_info = build_legislator_info(legislator_data)
        legislator_votes = legislator_data.get("votes", [])

        profile = create_legislator_profile(
            legislator_info, legislator_votes, bill_analyses
        )

        # Don't add profiles for legislator's that had no votes with the active filters
        if profile["vote_count"] == 0:
            continue

        # Add unique metadata
        profile["model"] = model
        profile["schema_version"] = schema
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


def get_spec_hash(model, schema, congress=None, chamber=None, bill_type=None) -> str:
    """Generate a readable spec hash like: model_schema_congress_chamber_billtype.

    Model and schema are required. Congress, chamber, and bill_type are optional.
    """
    # If not specified, latest was used
    if schema is None:
        schema = SCHEMA_VERSION

    parts = [model, str(schema)]
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


def load_bill_analyses_from_db(
    model, schema_version=None, congress=None, bill_type=None
):
    """
    Load bill analyses from MongoDB, filtering by model and optionally schema version.
    """
    bill_analyses = {}
    collection = db_utils.get_collection(INPUT_COLLECTION)

    if schema_version is None:
        # Get latest schema version available
        schema_version = SCHEMA_VERSION

    query = {"model": model, "schema_version": schema_version}

    # Optional filters
    if congress:
        query["congress"] = str(congress)
    if bill_type:
        query["bill_type"] = {"$in": bill_type}

    for analysis_data in collection.find(query):
        bill_id = analysis_data.get("bill_id")
        if bill_id:
            if bill_id not in bill_analyses:
                bill_analyses[bill_id] = analysis_data

    print(
        f"Loaded {len(bill_analyses)} bill analyses for model '{model}' (schema v{schema_version})"
    )
    return bill_analyses


def generate_rankings(profiles):
    """Generate rankings for each category/spectrum across all profiles."""
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
        for field in ["primary_categories"]:
            data = profile.get(field, {})

            for category, details in data.items():

                # Don't include if bill count is too low (not enough data yet)
                if details.get("bill_count", 0) <= 10:
                    continue

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
        method="dense", ascending=False
    )
    df["percentile_rank"] = df.groupby(["field", "category"])["score"].rank(
        pct=True, ascending=True
    )
    current_df["current_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(method="dense", ascending=False)
    current_df["current_percentile_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(pct=True, ascending=True)

    # Now push results back into each profile
    for profile in profiles:
        is_current = profile["member_id"] in current_legislators
        for field in ["primary_categories"]:
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


def write_profiles_to_json(profiles):
    """Write legislator profiles to JSON files in data/legislator_profiles."""
    raise DeprecationWarning
    count = 0
    for profile in profiles:
        output_file = OUTPUT_DIR / f"{profile['member_id']}.json"
        try:
            with open(output_file, "w") as f:
                json.dump(profile, f, indent=2)
            count += 1
        except Exception as e:
            print(f"Failed to write profile for {profile['name']} to file: {e}")
    print(f"Updated profile for {count} members")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Deprecated
    parser.add_argument(
        "--data",
        action="store_true",
        help="Store to data/ instead of MongoDB",
    )
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

    # Check if bill analyses is empty
    if bill_analyses:

        # Get hash
        spec_hash = get_spec_hash(
            args.model, args.schema, args.congress, args.chamber, args.bill_type
        )

        # Process all legislators
        profiles = process_all_legislators(
            bill_analyses, args.model, spec_hash, args.schema, args.chamber
        )

        # Create rankings for each category/spectrum
        profiles = generate_rankings(profiles)

        # Write profiles
        if args.data:
            write_profiles_to_json(profiles)
        else:
            write_profiles_to_db(profiles)
    else:
        print(
            f"No analyses were found for model: {args.model} with schema version: {args.schema if args.schema else 'latest'}"
        )
