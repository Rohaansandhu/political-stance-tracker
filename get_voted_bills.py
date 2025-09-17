import os
import json
import subprocess
from pathlib import Path

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data/119/votes/2025")

def fetch_bill_status(bill_id: str, congress: str):
    # filter regex
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


def get_bills():
    seen_bills = set()

    # Iterate through each vote folder under 2025
    for folder in CONGRESS_DATA_DIR.iterdir():
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


if __name__ == "__main__":
    get_bills()