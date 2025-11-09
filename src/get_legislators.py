import requests
import os
import json
import db.db_utils as db_utils
import argparse


def get_current_legislators():
    """Fetch current legislators JSON and save to data/current_legislators.json"""

    # Load JSON data 
    currentUrl = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

    response = requests.get(currentUrl)
    response.raise_for_status()  # raises an error if request failed
    data = response.json()

    # Make sure output directory exists
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    # File path
    json_path = os.path.join(output_dir, "current_legislators.json")

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"JSON file created at {json_path}")

def get_historical_legislators():
    """Fetch historical legislators JSON and save to data/historical_legislators.json"""

    # Load JSON data 
    historicalUrl = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"

    response = requests.get(historicalUrl)
    response.raise_for_status()  # raises an error if request failed
    data = response.json()

    # Make sure output directory exists
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    # File path
    json_path = os.path.join(output_dir, "historical_legislators.json")

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"JSON file created at {json_path}")

def add_legislators_to_db():
    """Fetch current and historical legislators and add to MongoDB collection"""

    # Load JSON data from files
    with open("data/current_legislators.json", "r") as f:
        current_data = json.load(f)

    with open("data/historical_legislators.json", "r") as f:
        historical_data = json.load(f)

    # Combine data
    all_legislators = current_data + historical_data

    # Insert or update legislators in the database
    for legislator in all_legislators:
        # extract bioguide ID for indexing
        bioguide_id = legislator.get("id", {}).get("bioguide")
        if not bioguide_id:
            continue 

        legislator["bioguide"] = bioguide_id
        filter = {"bioguide": bioguide_id}
        db_utils.update_one("legislators", legislator, filter)

    print(f"Inserted/Updated {len(all_legislators)} legislators in the database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Update legislator jsons before adding to DB")
    args = parser.parse_args()
    if args.update:
        get_historical_legislators()
        get_current_legislators()
    add_legislators_to_db()