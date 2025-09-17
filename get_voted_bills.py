import os
import json
import subprocess
from pathlib import Path

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")

def fetch_bill_status(bill_id: str, congress: str):
    # Use regex to ensure exact matches
    bill_id_regex = f"^{bill_id}$"

    # Build the command as a list of arguments
    cmd = [
        "usc-run",
        "govinfo",
        "--bulkdata=BILLSTATUS",
        f"--congress={congress}",
        f"--filter={bill_id_regex}"
    ]

    try:
        # Run the command
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)

def build_bill_id(bill: dict) -> str:
    """
    Turn a bill object into a filter string for usc-run.
    Example: {"congress":119,"number":242,"type":"hres"} 
             -> "BILLSTATUS-119hres242"
    """
    congress = bill["congress"]
    number = bill["number"]
    btype = bill["type"].lower()  # ensure lowercase
    return f"BILLSTATUS-{congress}{btype}{number}"

def generate_bill_jsons():
    """ Generate the data.json for every bill xml data pulled """

    # Create command
    cmd = [
        "usc-run",
        "bills"
    ]

    try:
        # Run the command
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)


def get_bills():
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

                # Build filter string
                bill_id = build_bill_id(bill)

                # Don't fetch duplicates
                if bill_id in seen_bills:
                    continue

                print(f"Fetching bill status for {bill_id}")

                # Call govinfo downloader
                fetch_bill_status(bill_id, bill["congress"])

                seen_bills.add(bill_id)
    
    # Generate data.json files for all bill xml data pulled
    generate_bill_jsons()


if __name__ == "__main__":
    get_bills()