import argparse
import time
import json
from pathlib import Path
import bill_analysis_client
import db_utils
from load_to_db import load_bills_and_analyses

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")

# MODEL CHOICE
# Free options: x-ai/grok-4-fast:free, deepseek/deepseek-chat-v3.1:free, google/gemini-2.0-flash-exp:free,
# openai/gpt-oss-120b:free, z-ai/glm-4.5-air:free openai/gpt-oss-20b:free, meta-llama/llama-3.3-8b-instruct:free
# Using gemini-2.5-flash-lite because of openrouter rate-limiting on free models
MODEL = "gemini-2.5-flash-lite"


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


def generate_bill_analyses(force=False, num_of_bills=None, no_db=False, only_db=False):
    generated_bills = set()
    start_time = time.perf_counter()

    if not only_db:
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

                if bill_type_folder.name not in ["hr", "hjres", "s", "sjres"]:
                    continue

                for folder in bill_type_folder.iterdir():
                    if not folder.is_dir():
                        continue

                    data_file = folder / "data.json"
                    if not data_file.exists():
                        continue

                    is_voted = folder / "voted_bill.txt"
                    if not is_voted.exists():
                        continue

                    bill_analysis_file = folder / "bill_analysis.json"
                    if bill_analysis_file.exists() and not force:
                        if check_schema_version(bill_analysis_file):
                            print(
                                f"{folder.name} has current analysis (schema v{bill_analysis_client.SCHEMA_VERSION}) - skipping"
                            )
                            continue
                        else:
                            print(
                                f"{folder.name} has outdated schema - regenerating analysis"
                            )

                    with open(data_file, "r") as f:
                        bill_data = json.load(f)

                    summary_data = bill_data.get("summary")
                    if not summary_data:
                        print(f"ERROR: Couldn't find summary data for {folder.name}")
                        continue
                    summary_text = summary_data.get("text")
                    if summary_text == "":
                        print(f"ERROR: Couldn't find summary text for {folder.name}")
                        continue

                    # Call LLM Client
                    bill_analysis = bill_analysis_client.analyze_bill(
                        summary_text, model=MODEL
                    )

                    # bill_id for a file found in data/117/bills/hr/hr1 is hr1-117
                    # Add bill_id to data
                    bill_id = bill_data["bill_id"]
                    bill_analysis["bill_id"] = bill_id
                    # Add model info to data
                    bill_analysis["model"] = MODEL

                    # Write data
                    out_file = folder / "bill_analysis.json"
                    with open(out_file, "w") as f:
                        json.dump(bill_analysis, f, indent=2)

                    generated_bills.add(bill_id)
                    print(f"{bill_id} bill analysis generated")

                    # Stop if we've reached the specified number of bills
                    if (
                        num_of_bills is not None
                        and len(generated_bills) >= num_of_bills
                    ):
                        print(f"Reached specified number of bills: {num_of_bills}")
                        end_time = time.perf_counter()
                        print(f"Elapsed time: {end_time - start_time} seconds")
                        return generated_bills

    # Process from MongoDB if specified
    if not no_db:
        bill_collection = db_utils.get_collection("bill_data").find()
        print("\nFetching bills from MongoDB bill_data collection...")
        bill_analyses_coll = db_utils.get_collection("bill_analyses")

        existing_ids = set(bill_analyses_coll.distinct("bill_id"))

        for bill_data in bill_collection:
            bill_id = bill_data.get("bill_id")
            # Check if analysis already exists
            if bill_id in existing_ids and not force:
                print(f"Bill analysis for {bill_id} already exists, skipping...")
                continue

            # These are bills that just got analyzed from the data/ directory
            if bill_id in generated_bills:
                continue

            summary_data = bill_data.get("summary")
            if not summary_data:
                print(f"ERROR: Couldn't find summary data for {bill_id}")
                continue
            summary_text = summary_data.get("text")
            if summary_text == "":
                print(f"ERROR: Couldn't find summary text for {bill_id}")
                continue

            bill_congress = bill_data.get("congress")
            bill_type = bill_data.get("bill_type")
            bill_number = bill_data.get("number")

            # Call LLM Client
            bill_analysis = bill_analysis_client.analyze_bill(summary_text, model=MODEL)

            # Add bill_id
            bill_analysis["bill_id"] = bill_id

            out_file = (
                CONGRESS_DATA_DIR
                / str(bill_congress)
                / "bills"
                / bill_type
                / f"{bill_type}{str(bill_number)}"
                / "bill_analysis.json"
            )
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w") as f:
                json.dump(bill_analysis, f, indent=2)

            generated_bills.add(bill_id)
            print(f"{bill_id} bill analysis generated")

            # Stop if we've reached the specified number of bills
            if num_of_bills is not None and len(generated_bills) >= num_of_bills:
                print(f"Reached specified number of bills: {num_of_bills}")
                break

    end_time = time.perf_counter()
    print(f"Elapsed time: {end_time - start_time} seconds")
    return generated_bills


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-download")
    parser.add_argument(
        "--no_db",
        action="store_true",
        help="Don't load the data to/from the db, just process and save to data/ folder",
    )
    parser.add_argument(
        "--numOfBills",
        type=int,
        help="Number of bills to process, since this can be time consuming",
    )
    parser.add_argument(
        "--only_db",
        action="store_true",
        help="Only process bills from the database, skip local files",
    )

    args = parser.parse_args()
    bill_count = generate_bill_analyses(
        args.force, args.numOfBills, args.no_db, args.only_db
    )
    print(f"Total bill analyses generated: {len(bill_count)}")
    if not args.no_db:
        load_bills_and_analyses()
