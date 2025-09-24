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
    spectrum_scores = defaultdict(list)  # Store individual scores for averaging
    category_scores = defaultdict(list)  # Store category-specific votes
    
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
        
        # Skip non-voting records
        if vote.lower() in ["not voting", "present", ""]:
            continue
            
        # Determine vote direction (1 for support, -1 for oppose)
        vote_multiplier = get_vote_multiplier(vote)
        if vote_multiplier == 0:  # Did not vote
            continue
            
        # Process political spectrums
        political_spectrums = bill_analysis.get("political_spectrums", {})
        for spectrum_name, spectrum_data in political_spectrums.items():
            score = spectrum_data.get("score", 0)
            # Apply vote: support = add score, oppose = subtract score
            adjusted_score = score * vote_multiplier
            spectrum_scores[spectrum_name].append(adjusted_score)
        
        # Process categories (determine left/right leaning based on vote analysis)
        voting_analysis = bill_analysis.get("voting_analysis", {})
        primary_category = bill_analysis.get("political_categories", {}).get("primary", "")
        
        if primary_category and voting_analysis:
            category_alignment = determine_category_alignment(vote, voting_analysis)
            if category_alignment:
                category_scores[primary_category].append(category_alignment)
    
    # Calculate final scores
    final_spectrum_scores = {}
    for spectrum_name, scores in spectrum_scores.items():
        if scores:
            final_spectrum_scores[spectrum_name] = round(sum(scores) / len(scores), 3)
    
    # Calculate category classifications
    final_categories = {}
    for category, alignments in category_scores.items():
        if alignments:
            avg_score = sum(alignments) / len(alignments)
            final_categories[category] = "left" if avg_score < -0.1 else "right" if avg_score > 0.1 else "center"
    
    return {
        "spectrum_scores": final_spectrum_scores,
        "category_classifications": final_categories,
        "vote_count": len([v for v in legislator_votes if v.get("vote", "").lower() not in ["not voting", "present", ""]])
    }

def get_vote_multiplier(vote):
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

def determine_category_alignment(vote, voting_analysis):
    """
    Determine if a vote aligns with left or right position for a category.
    Returns: -1 (left), +1 (right), or 0 (neutral/unclear)
    """
    vote_multiplier = get_vote_multiplier(vote)
    # Didn't vote
    if vote_multiplier == 0:
        return 0
    
    yes_vote_analysis = voting_analysis.get("yes_vote", {})
    yes_position = yes_vote_analysis.get("political_position", "").lower()
    
    # If voting YES and the analysis says YES is conservative/right
    if vote_multiplier == 1:  # Voted YES
        if "conservative" in yes_position or "right" in yes_position:
            return 1  # Right alignment
        elif "liberal" in yes_position or "progressive" in yes_position or "left" in yes_position:
            return -1  # Left alignment
    
    # If voting NO, flip the alignment
    elif vote_multiplier == -1:  # Voted NO
        if "conservative" in yes_position or "right" in yes_position:
            return -1  # Opposing conservative = left alignment
        elif "liberal" in yes_position or "progressive" in yes_position or "left" in yes_position:
            return 1  # Opposing liberal = right alignment
    
    return 0  # Unclear alignment

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
        "categories": ideology_data["category_classifications"],
        "detailed_spectrums": ideology_data["spectrum_scores"],
        "vote_count": ideology_data["vote_count"]
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
    "Individualism vs Collectivism"
    ]
    left_right_values = []
    
    for spectrum in left_right_spectrums:
        if spectrum in spectrum_scores:
            left_right_values.append(spectrum_scores[spectrum])
    
    if left_right_values:
        standard_scores["left_right"] = round(sum(left_right_values) / len(left_right_values), 3)
    
    # Map to authoritarian-libertarian scale (if you have relevant spectrums)
    auth_lib_spectrums = [
    "Federalism",
    "Civil Liberties vs Security",
    "Democracy vs Authoritarianism",
    "Populism vs Elitism",
    ]
    auth_lib_values = []
    
    for spectrum in auth_lib_spectrums:
        if spectrum in spectrum_scores:
            auth_lib_values.append(spectrum_scores[spectrum])
    
    if auth_lib_values:
        standard_scores["authoritarian_libertarian"] = round(sum(auth_lib_values) / len(auth_lib_values), 3)
    
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
        
        profile = create_legislator_profile(legislator_info, legislator_votes, bill_analyses)
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
    