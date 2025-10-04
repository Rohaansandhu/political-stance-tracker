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


def check_requirements(bill_id, bill_analyses_coll):
    """
    Check requirements for generating a new bill_analysis.
    Currently the requirements are that the schema is outdated, or the model is new
    """
    # Get reference (check schema and model here)
    existing_analysis = bill_analyses_coll.find_one(
        {
            "bill_id": bill_id,
            "model": MODEL,
            "schema_version": bill_analysis_client.SCHEMA_VERSION,
        }
    )
    if existing_analysis:
        print(f"{bill_id} has correct schema version and model - skipping")
        return False

    # No reference of bill was found, therefore we should generate
    return True


def generate_bill_analyses(force=False, num_of_bills=None):
    generated_ids = set()
    start_time = time.perf_counter()

    # Process from MongoDB
    bill_collection = db_utils.get_collection("bill_data").find()
    print("\nFetching bills from MongoDB bill_data collection...")
    bill_analyses_coll = db_utils.get_collection("bill_analyses")

    existing_ids = set(bill_analyses_coll.distinct("bill_id"))

    for bill_data in bill_collection:
        bill_id = bill_data.get("bill_id")
        # Check if analysis already exists
        if bill_id in existing_ids and not force:
            should_generate = check_requirements(bill_id, bill_analyses_coll)
            if not should_generate:
                continue

        # Avoid duplicates
        if bill_id in generated_ids:
            continue

        summary_data = bill_data.get("summary")
        if not summary_data:
            print(f"ERROR: Couldn't find summary data for {bill_id}")
            continue
        summary_text = summary_data.get("text")
        if summary_text == "":
            print(f"ERROR: Couldn't find summary text for {bill_id}")
            continue

        # Call LLM Client
        bill_analysis = bill_analysis_client.analyze_bill(summary_text, model=MODEL)

        # Add bill_id
        bill_analysis["bill_id"] = bill_id
        # Add model
        bill_analysis["model"] = MODEL

        filter = {
            "bill_id": bill_id,
            "model": MODEL,
            "schema_version": bill_analysis_client.SCHEMA_VERSION,
        }

        # Load bill_analysis to the db
        db_utils.update_one("bill_analyses", bill_analysis, filter)

        # Add bill analysis and id (for checking dups)
        generated_ids.add(bill_id)
        print(f"{bill_id} bill analysis generated")

        # Stop if we've reached the specified number of bills
        if num_of_bills is not None and len(generated_ids) >= num_of_bills:
            print(f"Reached specified number of bills: {num_of_bills}")
            break

    end_time = time.perf_counter()
    print(f"Elapsed time: {end_time - start_time} seconds")
    return generated_ids


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force re-download")
    parser.add_argument(
        "--numOfBills",
        type=int,
        help="Number of bills to process, since this can be time consuming",
    )
    args = parser.parse_args()

    generated_bills = generate_bill_analyses(args.force, args.numOfBills)
    print(f"Total bill analyses generated: {len(generated_bills)}")
