import argparse
import datetime
import re
import json
import subprocess
from pathlib import Path

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")


def mark_bill_as_voted(folder_location):
    """
    Mark a bill as voted by adding a txt file.
    Check if a bill is voted on, by checking the existence of this file
    in the bills folder
    """
    file_path = folder_location / "voted_bill.txt"
    with open(file_path, "w") as f:
        f.write(
            f"processed: {datetime.datetime.now(datetime.timezone.utc).isoformat()}Z"
        )


def fetch_bill_status(bill_id: str, congress: str):
    # Use regex to ensure exact matches
    bill_id_regex = re.escape(bill_id) + r"\.xml$"

    # Build the command as a list of arguments
    cmd = [
        "usc-run",
        "govinfo",
        "--bulkdata=BILLSTATUS",
        f"--congress={congress}",
        f"--filter={bill_id_regex}",
    ]

    try:
        # Run the command
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)
        return False


def build_billstatus_id(bill: dict) -> str:
    """
    Turn a bill object into a filter string for usc-run.
    Example: {"congress":119,"number":242,"type":"hres"}
             -> "BILLSTATUS-119hres242"
    """
    congress = bill["congress"]
    number = bill["number"]
    btype = bill["type"].lower()  # ensure lowercase
    return f"BILLSTATUS-{congress}{btype}{number}"


def generate_bill_jsons(force=False):
    """Generate the data.json for every bill xml data pulled"""

    # Create command
    cmd = ["usc-run", "bills"]

    # Forces re-parsing of all data
    if force:
        cmd.append("--force")

    try:
        # Run the command
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)


def get_bills(force=False):
    seen_bills = set()

    # Example path to roll call data: data/{congress}/votes/{year}
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
                    continue

                data_file = folder / "data.json"
                if not data_file.exists():
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

                # Build filter string
                bill_id = build_billstatus_id(bill)

                # Don't fetch duplicates
                if bill_id in seen_bills:
                    continue

                print(f"Fetching bill status for {bill_id}")

                # Call govinfo downloader (returns True upon success)
                result = fetch_bill_status(bill_id, bill["congress"])

                # Mark bill as voted if fetch bill status finished with no errors
                if result:
                    # Construct bill folder file path and handle errors
                    congress_dir = congress / "bills"
                    if not congress_dir.is_dir():
                        print(f"WARNING: No bills folder in {congress}")
                        continue
                    bill_type_dir = congress_dir / bill["type"].lower()
                    if not bill_type_dir.is_dir():
                        print(f"WARNING: No bill type folder in {bill_id}")
                        continue
                    bill_dir = bill_type_dir / (
                        bill["type"].lower() + str(bill["number"])
                    )
                    if not bill_dir.is_dir():
                        print(f"WARNING: No specific bill folder in {bill_id}")
                        continue

                    mark_bill_as_voted(bill_dir)

                seen_bills.add(bill_id)

    # Generate data.json files for all bill xml data pulled
    if force:
        generate_bill_jsons(True)
    else:
        generate_bill_jsons(False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-download")

    args = parser.parse_args()

    get_bills(args.force)
