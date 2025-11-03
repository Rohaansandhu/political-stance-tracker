import argparse
import json
from collections import defaultdict
from pathlib import Path
from bill_analysis_client import SCHEMA_VERSION
import sys
# Add src/ to import path
SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parent 
sys.path.insert(0, str(SRC_DIR)) 
import db.db_utils as db_utils


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
    spectrum_votes = defaultdict(list)
    primary_category_votes = defaultdict(list)
    secondary_category_votes = defaultdict(list)
    subcategory_votes = defaultdict(list)
    # Combined primary + secondary
    main_category_votes = defaultdict(list)

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

        key_provisions = bill_analysis.get("bill_summary", {}).get("key_provisions", [])

        # Process spectrums - single iteration
        for spectrum_name, spectrum_data in bill_analysis.get(
            "political_spectrums", {}
        ).items():
            partisan_score = spectrum_data.get("partisan_score", 0)
            impact_score = spectrum_data.get("impact_score", 0)

            spectrum_votes[spectrum_name].append(
                {
                    "bill_id": bill_id,
                    "vote": vote,
                    "vote_value": vote_value,
                    "key_provisions": key_provisions,
                    "partisan_score": partisan_score,
                    "impact_score": impact_score,
                    "weighted_score": partisan_score * impact_score * vote_value,
                }
            )

        # Process categories - single iteration
        political_categories = bill_analysis.get("political_categories", {})

        # Primary category
        primary_category = political_categories.get("primary", {})
        if isinstance(primary_category, dict) and primary_category.get("name"):
            category_name = primary_category.get("name", "")
            partisan_score = primary_category.get("partisan_score", 0.0)
            impact_score = primary_category.get("impact_score", 0.0)
            weighted_score = partisan_score * impact_score * vote_value

            vote_data = {
                "bill_id": bill_id,
                "vote": vote,
                "vote_value": vote_value,
                "key_provisions": key_provisions,
                "partisan_score": partisan_score,
                "impact_score": impact_score,
                "weighted_score": weighted_score,
            }

            primary_category_votes[category_name].append(vote_data)
            main_category_votes[category_name].append(vote_data)

        # Secondary categories
        for secondary_category in political_categories.get("secondary", []):
            if isinstance(secondary_category, dict) and secondary_category.get("name"):
                category_name = secondary_category.get("name", "")
                partisan_score = secondary_category.get("partisan_score", 0.0)
                impact_score = secondary_category.get("impact_score", 0.0)
                weighted_score = partisan_score * impact_score * vote_value

                vote_data = {
                    "bill_id": bill_id,
                    "vote": vote,
                    "vote_value": vote_value,
                    "key_provisions": key_provisions,
                    "partisan_score": partisan_score,
                    "impact_score": impact_score,
                    "weighted_score": weighted_score,
                }

                secondary_category_votes[category_name].append(vote_data)
                main_category_votes[category_name].append(vote_data)

        # Subcategories
        for subcategory in political_categories.get("subcategories", []):
            if isinstance(subcategory, dict) and subcategory.get("name"):
                category_name = subcategory.get("name", "")
                partisan_score = subcategory.get("partisan_score", 0.0)
                impact_score = subcategory.get("impact_score", 0.0)
                weighted_score = partisan_score * impact_score * vote_value

                subcategory_votes[category_name].append(
                    {
                        "bill_id": bill_id,
                        "vote": vote,
                        "vote_value": vote_value,
                        "key_provisions": key_provisions,
                        "partisan_score": partisan_score,
                        "impact_score": impact_score,
                        "weighted_score": weighted_score,
                    }
                )
        vote_count += 1

    return {
        "spectrum_scores": calculate_average_scores(spectrum_votes),
        "main_category_classifications": calculate_average_scores(main_category_votes),
        "primary_category_classifications": calculate_average_scores(
            primary_category_votes
        ),
        "secondary_category_classifications": calculate_average_scores(
            secondary_category_votes
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
        "main_categories": ideology_data["main_category_classifications"],
        "primary_categories": ideology_data["primary_category_classifications"],
        "secondary_categories": ideology_data["secondary_category_classifications"],
        "subcategories": ideology_data["subcategory_classifications"],
        "detailed_spectrums": ideology_data["spectrum_scores"],
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


def process_all_legislators(bill_analyses, model, spec_hash, schema=None):
    """Process ideology scores for all legislators"""

    # If schema not specified, use latest version
    if schema is None:
        schema = SCHEMA_VERSION

    profiles = []
    # Get collection
    member_votes_col = db_utils.get_collection("member_votes")
    for legislator_data in member_votes_col.find():
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
    model, schema_version=None, congress=None, chamber=None, bill_type=None
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
    if chamber:
        query["chamber"] = chamber
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


def write_profiles_to_db(profiles):
    """Write legislator profiles to MongoDB collection."""
    count = 0
    for profile in profiles:
        try:
            query = {
                "member_id": profile["member_id"],
                "model": profile["model"],
                "schema_version": profile["schema_version"],
                "spec_hash": profile["spec_hash"],
            }
            db_utils.update_one(OUTPUT_COLLECTION, profile, query)
            count += 1
        except Exception as e:
            print(f"Failed to insert/update profile for {profile['name']}: {e}")
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
        args.model, args.schema, args.congress, args.chamber, args.bill_type
    )

    # Check if bill analyses is empty
    if bill_analyses:

        # Get hash
        spec_hash = get_spec_hash(
            args.model, args.schema, args.congress, args.chamber, args.bill_type
        )

        # Process all legislators
        profiles = process_all_legislators(
            bill_analyses, args.model, spec_hash, args.schema
        )

        # Write profiles
        if args.data:
            write_profiles_to_json(profiles)
        else:
            write_profiles_to_db(profiles)
    else:
        print(
            f"No analyses were found for model: {args.model} with schema version: {args.schema if args.schema else 'latest'}"
        )
