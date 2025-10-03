# Script to process roll call votes and organize them by member.
# Can read from data/ directory or MongoDB, and outputs to data/organized_votes or MongoDB.
import os
import json
from pathlib import Path
import argparse
import db_utils


# Path to the data directory
CONGRESS_DATA_DIR = Path("data")
INPUT_COLLECTION = "rollcall_votes"

# Output directory for member-organized data
OUTPUT_DIR = Path("data/organized_votes")
OUTPUT_DIR.mkdir(exist_ok=True)


def write_member_votes_to_data(member_votes):
    """Write member votes to data/organized_votes as {member_id}.json files"""
    for member_id, data in member_votes.items():
        out_file = OUTPUT_DIR / f"{member_id}.json"
        with open(out_file, "w") as f:
            json.dump(data, f, indent=2)

    print(f"Created {len(member_votes)} member JSON files in {OUTPUT_DIR}")


def write_member_votes_to_db(member_votes):
    """Write member votes to MongoDB 'member_votes' collection"""

    count = 0
    for data in member_votes.values():
        db_utils.update_one("member_votes", data, "member_id")
        count += 1

    print(f"Inserted/Updated {count} member vote documents into the database.")

def process_vote_record(vote_data, member_votes):
    """Process a single vote record and add to member_votes dict"""
    # Filter out non-bill votes
    if (bill := vote_data.get("bill", {})) == {}:
        return
    
    # Ignore non-legislative bills
    if bill.get("type") not in ["hr", "hjres", "s", "sjres"]:
        return
    
    # Only include passage votes
    category = vote_data.get("category")
    if category not in ["passage", "passage-suspension"]:
        return
    
    # Extract metadata
    vote_id = vote_data.get("vote_id")
    chamber = vote_data.get("chamber")
    date = vote_data.get("date")
    question = vote_data.get("question")
    subject = vote_data.get("subject")
    source_url = vote_data.get("source_url")
    
    # Process each member's vote
    votes = vote_data.get("votes", {})
    for pos, members in votes.items():
        for member in members:
            if member == "VP":
                continue
            
            member_id = member.get("id")
            if not member_id:
                continue
            
            if member_id not in member_votes:
                member_votes[member_id] = {
                    "member_id": member_id,
                    "name": member.get("display_name"),
                    "party": member.get("party"),
                    "state": member.get("state"),
                    "votes": [],
                }
            
            member_votes[member_id]["votes"].append({
                "vote_id": vote_id,
                "bill": bill,
                "chamber": chamber,
                "date": date,
                "category": category,
                "question": question,
                "subject": subject,
                "source_url": source_url,
                "vote": pos,
            })


def process_votes_from_data():
    member_votes = {}

    # Example path to roll call data: data/{congress_num}/votes/{year}
    for congress in CONGRESS_DATA_DIR.iterdir():
        if not congress.is_dir():
            continue

        votes_dir = congress / "votes"
        if not votes_dir.exists():
            print(f"WARNING: No votes folder in {congress}")
            continue

        # Iterate through each vote folder by year
        for year in votes_dir.iterdir():
            if not year.is_dir():
                continue

            # Iterate through all roll call vote folders in year
            for folder in year.iterdir():
                if not folder.is_dir():
                    print(f"ERROR: Not a directory: {folder.name}")
                    continue

                data_file = folder / "data.json"
                if not data_file.exists():
                    print(f"ERROR: Couldn't find data.json for {folder.name}")
                    continue

                with open(data_file, "r") as f:
                    vote_data = json.load(f)

                process_vote_record(vote_data, member_votes)
    return member_votes


def process_votes_from_db():
    member_votes = {}
    rollcall_votes = db_utils.get_collection(INPUT_COLLECTION)
    print(f"Found {rollcall_votes.count_documents({})} rollcall votes in the database")
    rollcall_votes = rollcall_votes.find()
    for vote_data in rollcall_votes:
        process_vote_record(vote_data, member_votes)
    return member_votes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        choices=["mongodb", "data", "both"],
        default="mongodb",
        help="Source of roll call votes: MongoDB or data/ or both. Specify --input mongodb or --input data or --input both",
    )
    parser.add_argument(
        "--output",
        choices=["mongodb", "data", "both"],
        default="both",
        help="Store to MongoDB or data/. Specify --output mongodb or --output data",
    )

    args = parser.parse_args()
    # process votes
    if args.input == "both":
        vote_data = process_votes_from_data()
        vote_data.update(process_votes_from_db())
    elif args.input == "data":
        vote_data = process_votes_from_data()
    else:
        vote_data = process_votes_from_db()

    # output votes
    if args.output == "data":
        write_member_votes_to_data(vote_data)
    elif args.output == "mongodb":
        write_member_votes_to_db(vote_data)
    else:
        write_member_votes_to_data(vote_data)
        write_member_votes_to_db(vote_data)
