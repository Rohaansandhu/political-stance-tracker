# Script to load all data from the data/ directory into the MongoDB database.
# Feel free to comment out functions you don't want to run, each function corresponds to a collection
import os
import json
from pathlib import Path
from bill_analysis_client import SCHEMA_VERSION
import db_utils

DATA_DIR = Path("data")


def load_json_file(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_bills_and_analyses():
    count = 0
    for congress_dir in DATA_DIR.iterdir():
        if not congress_dir.is_dir() or not congress_dir.name.isdigit():
            continue
        bills_dir = congress_dir / "bills"
        if not bills_dir.exists():
            continue

        # Iterate over all subfolders (hr, s, sjres, hjres)
        for sub_bill_dir in bills_dir.iterdir():
            if not sub_bill_dir.is_dir():
                continue
            for sub_dir in sub_bill_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                voted_file = sub_dir / "voted_bill.txt"
                if not voted_file.exists():
                    continue
                # Insert data.json -> bill_data collection
                data_file = sub_dir / "data.json"
                if not data_file.exists():
                    continue
                try:
                    data = load_json_file(data_file)
                    db_utils.update_one("bill_data", data, "bill_id")
                except Exception as e:
                    print(f"Failed to load {data_file}: {e}")
                    continue
                # Insert bill_analysis.json -> bill_analyses collection
                analysis_file = sub_dir / "bill_analysis.json"
                if not analysis_file.exists():
                    continue
                try:
                    analysis_obj = load_json_file(analysis_file)
                    # bill_id for a file found in data/117/bills/hr/hr1 is hr1-117
                    analysis_obj["bill_id"] = sub_dir.name + "-" + congress_dir.name
                    # Ensure we only put the most updated bill analyses in the db
                    if analysis_obj.get("schema_version") != SCHEMA_VERSION:
                        bill_id = analysis_obj["bill_id"]
                        print(f"{bill_id} has an outdated schema")
                        continue
                    db_utils.update_one("bill_analyses", analysis_obj, "bill_id")
                except Exception as e:
                    print(f"Failed to load {analysis_file}: {e}")
                    continue
                count += 1
    print(f"Inserted {count} bills and analyses into the database.")


def load_votes():
    """
    Loads all data.json files found in each vote folder in each congress folder and inserts them into the 'votes' collection in the database.
    """
    count = 0
    for congress_dir in DATA_DIR.iterdir():
        # All congress folders are named by number (e.g., 117, 118, 119)
        if not congress_dir.is_dir() or not congress_dir.name.isdigit():
            continue

        votes_dir = congress_dir / "votes"
        if not votes_dir.exists():
            continue

        for year_dir in votes_dir.iterdir():
            if not year_dir.is_dir():
                continue

            for vote_folder in year_dir.iterdir():
                if not vote_folder.is_dir():
                    continue
                data_file = vote_folder / "data.json"
                if data_file.exists():
                    try:
                        data = load_json_file(data_file)
                        db_utils.update_one("rollcall_votes", data, "vote_id")
                        count += 1
                    except Exception as e:
                        print(f"Failed to load {data_file}: {e}")

    print(f"Inserted {count} vote files from all congress folders into the database.")


def load_member_organized_votes():
    """
    Loads all votes of each member from data/organized_votes and inserts them into the 'member_votes' collection in the db
    """
    organized_dir = DATA_DIR / "organized_votes"
    if not organized_dir.exists():
        print(f"Directory {organized_dir} does not exist.")
        return

    count = 0
    for member_file in organized_dir.iterdir():
        if member_file.suffix != ".json":
            continue
        try:
            member_data = load_json_file(member_file)
            db_utils.update_one("member_votes", member_data, "member_id")
            count += 1
        except Exception as e:
            print(f"Failed to load {member_file}: {e}")

    print(f"Inserted {count} member-organized vote files into the database.")


def load_legislator_profiles():
    """
    Loads all legislator profiles from data/legislator_profiles and inserts them into the 'legislator_profiles' collection in the db
    """
    profiles_dir = DATA_DIR / "legislator_profiles"
    if not profiles_dir.exists():
        print(f"Directory {profiles_dir} does not exist.")
        return

    count = 0
    for profile_file in profiles_dir.iterdir():
        if profile_file.suffix != ".json":
            continue
        try:
            profile_data = load_json_file(profile_file)
            db_utils.update_one("legislator_profiles", profile_data, "member_id")
            count += 1
        except Exception as e:
            print(f"Failed to load {profile_file}: {e}")

    print(f"Inserted {count} legislator profile files into the database.")


def main():
    print("Loading all data found in data/ into the database...")
    load_votes()
    load_bills_and_analyses()
    load_member_organized_votes()
    load_legislator_profiles()


if __name__ == "__main__":
    main()
