# Functions to load data from the data/ directory into the MongoDB database.
import json
from pathlib import Path
import db.db_utils as db_utils
from pymongo import UpdateOne

DATA_DIR = Path("data")


def load_json_file(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_bills():
    count = 0
    actions = []
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
                    actions.append(
                        UpdateOne(
                            {"bill_id": data["bill_id"]},
                            {"$set": data, "$currentDate": {"last_modified": True}},
                            upsert=True,
                        )
                    )
                except Exception as e:
                    print(f"Failed to load {data_file}: {e}")
                    continue
                count += 1

    if actions:
        db_utils.bulk_write("bill_data", actions)
    print(f"Inserted {count} bills into the database.")


def load_votes():
    """
    Loads all data.json files found in each vote folder in each congress folder and inserts them into the 'votes' collection in the database.
    """
    count = 0
    actions = []
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
                        actions.append(
                            UpdateOne(
                                {"vote_id": data["vote_id"]},
                                {"$set": data, "$currentDate": {"last_modified": True}},
                                upsert=True,
                            )
                        )
                        count += 1
                    except Exception as e:
                        print(f"Failed to load {data_file}: {e}")

    if actions:
        db_utils.bulk_write("rollcall_votes", actions)

    print(f"Inserted {count} vote files from all congress folders into the database.")
