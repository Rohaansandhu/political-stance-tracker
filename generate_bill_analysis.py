import argparse
import time
import json
from pathlib import Path
import bill_analysis_client

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")

def check_schema_version(bill_analysis_file):
    """Check if existing analysis has current schema version."""
    try:
        with open(bill_analysis_file, "r") as f:
            existing_analysis = json.load(f)
        
        # Check if schema_version exists and matches current version
        existing_version = existing_analysis.get("schema_version")
        return existing_version == bill_analysis_client.SCHEMA_VERSION
    
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        return False

def generate_bill_analyses(force=False):
    generated_bills = set()
    start_time = time.perf_counter()

    # Example path to roll call data: data/{congress}/bills/{bill_type}
    for congress in CONGRESS_DATA_DIR.iterdir():
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

            # Ignore hres, sres, hconres, sconres
            # These are simple and concurrent resolutions that have no lawful power
            if bill_type_folder.name not in ["hr", "hjres", "s", "sjres"]:
                continue

            # Iterate through all individual bill folders
            for folder in bill_type_folder.iterdir():
                if not folder.is_dir():
                    continue

                data_file = folder / "data.json"
                if not data_file.exists():
                    continue

                # Check if the bill was voted on
                is_voted = folder / "voted_bill.txt"
                if not is_voted.exists():
                    continue

                # Check if analysis exists and has current schema version
                bill_analysis_file = folder / "bill_analysis.json"                
                if bill_analysis_file.exists() and not force:
                    if check_schema_version(bill_analysis_file):
                        print(f"{folder.name} has current analysis (schema v{bill_analysis_client.SCHEMA_VERSION}) - skipping")
                        continue
                    else:
                        print(f"{folder.name} has outdated schema - regenerating analysis")

                with open(data_file, "r") as f:
                    bill_data = json.load(f)

                # Get Bill Summary Text
                summary_data = bill_data.get("summary")
                if not summary_data:
                    print(f"ERROR: Couldn't find summary data for {folder.name}")
                    continue
                summary_text = summary_data.get("text") 
                if summary_text == "":
                    print(f"ERROR: Couldn't find summary text for {folder.name}")
                    continue

                # Call LLM Client
                # Using grok for now, because gpt-oss was not working
                bill_analysis = bill_analysis_client.analyze_bill(summary_text, model="x-ai/grok-4-fast:free")

                # Write analysis to folder
                out_file = folder / "bill_analysis.json"
                with open(out_file, "w") as f:
                    json.dump(bill_analysis, f, indent=2)
                
                generated_bills.add(folder.name)
                print(f"{folder.name} bill analysis generated")
    end_time = time.perf_counter()
    print(f"Elapsed time: {end_time - start_time} seconds") 

    return generated_bills

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-download")

    args = parser.parse_args()
    bill_count = generate_bill_analyses(args.force)
    print(f"Total bill analyses generated: {len(bill_count)}")