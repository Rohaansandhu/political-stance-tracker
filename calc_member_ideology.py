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


def calculate_legislator_ideology(legislator_votes, bill_analyses):
    """
    Calculate ideology scores for a legislator based on their voting pattern.
    """

    # Initialize score accumulators
    spectrum_scores = defaultdict(list) 
    # Main categories (Combination of primary and secondary)
    main_category_scores = defaultdict(list)
    primary_category_scores = defaultdict(list)
    secondary_category_scores = defaultdict(list)
    subcategory_scores = defaultdict(list)

    # Process each vote
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
        # Did not vote
        if vote_value == 0:  
            continue

        # Process political spectrums
        political_spectrums = bill_analysis.get("political_spectrums", {})
        for spectrum_name, spectrum_data in political_spectrums.items():
            partisan_score = spectrum_data.get("partisan_score", 0)
            impact_score = spectrum_data.get("impact_score", 0)
            # Apply vote: support = add score, oppose = subtract score
            adjusted_score = partisan_score * impact_score * vote_value
            spectrum_scores[spectrum_name].append(adjusted_score)

        # Process categories (determine left/right leaning based on vote analysis)
        voting_analysis = bill_analysis.get("voting_analysis", {})
        political_categories = bill_analysis.get("political_categories", {})

        primary_category = political_categories.get("primary", {})
        if primary_category and voting_analysis:    
            if isinstance(primary_category, dict):
                category_name = primary_category.get("name", "")
                partisan_score = primary_category.get("partisan_score", 0.0)
                impact_score = primary_category.get("impact_score", 0.0)
                
                if category_name:
                    # Determines legislator alignment by multiplying vote direction (-1, 1, 0) with
                    # partisan score (-1 to 1) and impact score (0 to 1)
                    weighted_alignment = partisan_score * impact_score * vote_value
                    primary_category_scores[category_name].append(weighted_alignment)
                    main_category_scores[category_name].append(weighted_alignment)
            else: 
                print(f"WARNING: Unexpected primary category format: {bill_id} - {primary_category}")
        
        secondary_categories = political_categories.get("secondary", [])
        for secondary_category in secondary_categories:
            if isinstance(secondary_category, dict):
                category_name = secondary_category.get("name", "")
                partisan_score = primary_category.get("partisan_score", 0.0)
                impact_score = secondary_category.get("impact_score", 0.0)
                
                if category_name:
                    # Determines legislator alignment by multiplying vote direction (-1, 1, 0) with
                    # partisan score (-1 to 1) and impact score (0 to 1)
                    weighted_alignment = partisan_score * impact_score * vote_value
                    secondary_category_scores[category_name].append(weighted_alignment)
                    main_category_scores[category_name].append(weighted_alignment)
            else:
                print(f"WARNING: Unexpected secondary category format: {bill_id } - {secondary_category}")

        subcategories = political_categories.get("subcategories", [])
        for subcategory in subcategories:
            if isinstance(subcategory, dict):
                category_name = subcategory.get("name", "")
                partisan_score = primary_category.get("partisan_score", 0.0)
                impact_score = subcategory.get("impact_score", 0.0)
                
                if category_name:
                    # Determines legislator alignment by multiplying vote direction (-1, 1, 0) with
                    # partisan score (-1 to 1) and impact score (0 to 1)
                    weighted_alignment = partisan_score * impact_score * vote_value
                    subcategory_scores[category_name].append(weighted_alignment)
            else:   
                print(f"WARNING: Unexpected subcategory format: {bill_id} - {subcategory}")        

    # Calculate final scores
    final_spectrum_scores = {}
    for spectrum_name, scores in spectrum_scores.items():
        if scores:
            final_spectrum_scores[spectrum_name] = round(sum(scores) / len(scores), 3)

    # Calculate category classifications
    primary_final = {}
    for category, alignments in primary_category_scores.items():
        if alignments:
            avg_score = sum(alignments) / len(alignments)
            primary_final[category] = avg_score
    secondary_final = {}
    for category, alignments in secondary_category_scores.items():
        if alignments:
            avg_score = sum(alignments) / len(alignments)
            secondary_final[category] = avg_score
    subcategory_final = {}
    for category, alignments in subcategory_scores.items():
        if alignments:
            avg_score = sum(alignments) / len(alignments)
            subcategory_final[category] = avg_score
    main_final = {}
    for category, alignments in main_category_scores.items():
        if alignments:
            avg_score = sum(alignments) / len(alignments)
            main_final[category] = avg_score

    return {
        "spectrum_scores": final_spectrum_scores,
        "main_category_classifications": main_final,
        "primary_category_classifications": primary_final,
        "secondary_category_classifications": secondary_final,
        "subcategory_classifications": subcategory_final,
        "vote_count": len(
            [
                v
                for v in legislator_votes
                if v.get("vote", "").lower() not in ["not voting", "present", ""]
            ]
        ),
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
