import argparse
import datetime
import re
import subprocess
from pathlib import Path
import db.db_utils as db_utils
from db.load_to_db import load_bills

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


def get_bill_directory(congress_path, bill, show_warnings=True):
    """
    Helper function to get the bill directory path.
    Returns None if directory doesn't exist.
    """
    congress_dir = congress_path / "bills"
    if not congress_dir.is_dir():
        if show_warnings:
            print(f"WARNING: No bills folder in {congress_path}")
        return None

    bill_type_dir = congress_dir / bill["type"].lower()
    if not bill_type_dir.is_dir():
        if show_warnings:
            print(f"WARNING: No bill type folder for {bill['type']}")
        return None

    bill_dir = bill_type_dir / (bill["type"].lower() + str(bill["number"]))
    if not bill_dir.is_dir():
        if show_warnings:
            print(
                f"WARNING: No specific bill folder for {bill['type']}{bill['number']}"
            )
        return None

    return bill_dir


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


def get_bills(force=False, congress=None):
    seen_bills = set()

    # FETCH FROM MONGODB
    rollcall_collection = db_utils.get_collection("rollcall_votes")
    print("\nFetching bills from MongoDB rollcall_votes collection...")

    # Query for bill votes only (exclude non-bill votes)
    query = {
        "bill": {"$exists": True, "$ne": {}},
        "bill.type": {"$in": ["hr", "hjres", "s", "sjres"]},
        "category": {"$in": ["passage", "passage-suspension"]},
    }

    # Only include specified congress (optional)
    if congress:
        query["congress"] = int(congress)

    mongo_bill_count = rollcall_collection.count_documents(query)
    mongo_bills = rollcall_collection.find(query)
    print(f"Found {mongo_bill_count} rollcall votes")

    for vote_doc in mongo_bills:
        bill = vote_doc.get("bill", {})
        if not bill:
            continue

        # Build bill ID
        bill_id = build_billstatus_id(bill)

        # Skip if already processed
        if bill_id in seen_bills:
            continue

        congress = str(bill.get("congress"))
        congress_path = CONGRESS_DATA_DIR / congress

        bill_dir = get_bill_directory(congress_path, bill)
        if bill_dir:
            data_file = bill_dir / "data.json"
            if data_file.exists() and not force:
                # If data.json already exists and not forcing, skip
                print(
                    f"Skipping {bill['type']}{bill['number']} in {congress_path.name}, data.json already exists."
                )
                continue

        print(f"Fetching bill status for {bill_id} (from MongoDB)")

        # Fetch bill status, returns True upon success
        result = fetch_bill_status(bill_id, bill["congress"])

        if result:
            # Get congress number and construct path
            congress_num = bill["congress"]
            congress_path = CONGRESS_DATA_DIR / str(congress_num)

            bill_dir = get_bill_directory(congress_path, bill)
            if bill_dir:
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
    parser.add_argument("--congress", type=int, help="Only gets bills for a specific congress")

    args = parser.parse_args()

    get_bills(args.force, args.congress)

    # Will load bill_data collection into MongoDB
    load_bills()
