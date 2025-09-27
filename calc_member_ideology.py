import json
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path("data")
MEMBER_VOTES_DIR = Path("data/organized_votes")
OUTPUT_DIR = Path("data/legislator_profiles")
OUTPUT_DIR.mkdir(exist_ok=True)


def build_bill_id(bill: dict) -> str:
    """
    Turn a bill object into a string for id.
    Example: {"congress":119,"number":242,"type":"hres"}
             -> "119hres242"
    """
    if bill == {}:
        return ""
    congress = bill["congress"]
    number = bill["number"]
    btype = bill["type"].lower()  # ensure lowercase
    return f"{congress}{btype}{number}"


def calculate_average_scores(vote_data_dict):
    """Helper to calculate average weighted scores from vote data."""
    return {
        category: round(sum(vote["weighted_score"] for vote in votes) / len(votes), 3)
        for category, votes in vote_data_dict.items()
        if votes  # Only include categories with votes
    }


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
        if vote_value == 0:  # Did not vote
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
        "vote_count": len(
            [
                v
                for v in legislator_votes
                if v.get("vote", "").lower() not in ["not voting", "present", ""]
            ]
        ),
        "spectrum_impacts": spectrum_votes,
        "category_impacts": {
            **primary_category_votes,
            **secondary_category_votes,
            **subcategory_votes,
        },
    }


def get_vote_value(vote):
    """Convert vote string to multiplier."""
    vote_lower = vote.lower().strip()

    # Support votes
    if vote_lower in ["yea", "yes", "aye", "y"]:
        return 1

    # Opposition votes
    if vote_lower in ["nay", "no", "n"]:
        return -1

    # Didn't vote
    return 0


def create_legislator_profile(legislator_info, legislator_votes, bill_analyses):
    """
    Create complete legislator ideology profile.

    Args:
        legislator_info (dict): Basic info {"id": "A000360", "name": "Rep. X", "party": "D", "state": "CA"}
        legislator_votes (list): Vote records
        bill_analyses (dict): Bill analysis results

    Returns:
        dict: Complete legislator profile with ideology scores
    """

    ideology_data = calculate_legislator_ideology(legislator_votes, bill_analyses)

    # Create standardized spectrum scores (map to common left-right, authoritarian-libertarian)
    standard_scores = standardize_spectrum_scores(ideology_data["spectrum_scores"])

    profile = {
        "id": legislator_info.get("id"),
        "name": legislator_info.get("name"),
        "party": legislator_info.get("party"),
        "state": legislator_info.get("state"),
        "scores": standard_scores,
        "main_categories": ideology_data["main_category_classifications"],
        "primary_categories": ideology_data["primary_category_classifications"],
        "secondary_categories": ideology_data["secondary_category_classifications"],
        "subcategories": ideology_data["subcategory_classifications"],
        "detailed_spectrums": ideology_data["spectrum_scores"],
        "vote_count": ideology_data["vote_count"],
        "spectrum_impacts": ideology_data["spectrum_impacts"],
        "category_impacts": ideology_data["category_impacts"],
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
        "id": legislator_data.get("id"),
        "name": legislator_data.get("name"),
        "party": legislator_data.get("party"),
        "state": legislator_data.get("state"),
    }
    return legislator_info


def process_all_legislators(bill_analyses):
    """Process ideology scores for all legislators"""

    profiles = []
    # iterate over each legislator
    for legislator_file in MEMBER_VOTES_DIR.iterdir():
        if legislator_file.suffix != ".json":
            continue

        with open(legislator_file, "r") as f:
            legislator_data = json.load(f)

        legislator_info = build_legislator_info(legislator_data)
        legislator_votes = legislator_data.get("votes", [])

        profile = create_legislator_profile(
            legislator_info, legislator_votes, bill_analyses
        )
        profiles.append(profile)

        output_file = OUTPUT_DIR / f"{profile['id']}.json"
        with open(output_file, "w") as f:
            json.dump(profile, f, indent=2)

        print(f"Processed {profile['name']} ({profile['vote_count']} votes)")

    return profiles


def load_bill_analyses():
    """Load a dictionary of all bill analyses"""
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
            if bill_type_folder.name not in ["hr", "hjres", "s", "sjres"]:
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

                # Congress + folder = id, ex. 119 + hr26 = 119hr26
                bill_id = congress.name + folder.name

                bill_analyses[bill_id] = analysis_data

    return bill_analyses


# Example usage
if __name__ == "__main__":

    # Load your bill analyses
    bill_analyses = load_bill_analyses()

    # Process all legislators
    profiles = process_all_legislators(bill_analyses)
