import os
import json
from pathlib import Path

# Path to the data directory
CONGRESS_DATA_DIR = Path("data")

# Output directory for member-organized data
OUTPUT_DIR = Path("data/organized_votes")
OUTPUT_DIR.mkdir(exist_ok=True)

def process_votes():
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
                
                # Filter out non-bill votes (votes for quorum or leadership)
                if (bill := vote_data.get("bill", {})) == {}:
                    continue

                # Ignore hres, sres, hconres, sconres
                # These are simple and concurrent resolutions that have no lawful power
                if bill.get("type") not in ["hr", "hjres", "s", "sjres"]:
                    continue

                # Vote metadata
                vote_id = vote_data.get("vote_id")
                vote_number = vote_data.get("number")
                chamber = vote_data.get("chamber")
                date = vote_data.get("date")
                category = vote_data.get("category")
                question = vote_data.get("question")
                subject = vote_data.get("subject")
                source_url = vote_data.get("source_url")

                # Get all votes for the bill
                votes = vote_data.get("votes", {})
                # pos = "Nay", "Not Voting", "Yea"
                for pos, members in votes.items():
                    for member in members:
                        # Filter out Vice President votes
                        if member == "VP": 
                            continue
                        member_id = member.get("id")  
                        name = member.get("display_name")
                        party = member.get("party")
                        state = member.get("state")
                        vote = pos

                        if not member_id:
                            print(f"Couldn't find member id for {vote_number}")
                            continue

                        if member_id not in member_votes:
                            member_votes[member_id] = {
                                "id": member_id,
                                "name": name,
                                "party": party,
                                "state": state,
                                "votes": []
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
                            "vote": vote
                        })

    # Write out JSON per member
    for member_id, data in member_votes.items():
        out_file = OUTPUT_DIR / f"{member_id}.json"
        with open(out_file, "w") as f:
            json.dump(data, f, indent=2)

    print(f"Created {len(member_votes)} member JSON files in {OUTPUT_DIR}")

if __name__ == "__main__":
    process_votes()