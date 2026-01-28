import argparse
import time
from pathlib import Path
import os
from dotenv import load_dotenv
import analysis.bill_analysis_client as bill_analysis_client
import db.db_utils as db_utils


load_dotenv()

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")

# MODEL CHOICE (Specify in environment variables, or here)
# Models I've used:
# openrouter: deepseek/deepseek-chat-v3.1:free, x-ai/grok-4-fast:free, cognitivecomputations/dolphin-mistral-24b-venice-edition:free
# gemini: gemini-2.5-flash-lite (NOTE: bad partisan scores, lot's of censorship when it comes to politics)
# cerebras: gpt-oss-120b, llama3.3-70b, qwen-3-32b
MODEL = os.getenv("MODEL", "gpt-oss-120b")


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


def generate_bill_analyses(force=False, num_of_bills=None, delay=0.0):
    generated_ids = set()
    start_time = time.perf_counter()

    # Process from MongoDB
    bill_collection = db_utils.get_collection("bill_data").find(
        {"bill_type": {"$in": ["hr", "hjres", "s", "sjres"]}}
    )
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
        # Not required like summaries, but helpful for LLM to contextualize
        legislative_subjects = bill_data.get("subjects")
        top_subject = bill_data.get("subjects_top_term")

        # Call LLM Client
        bill_analysis = bill_analysis_client.analyze_bill(
            summary_text, legislative_subjects, top_subject, MODEL
        )

        # Add bill_id
        bill_analysis["bill_id"] = bill_id
        # Add model
        bill_analysis["model"] = MODEL
        # Using bill_data, add congress, chamber, and bill type
        bill_analysis["congress"] = bill_data["congress"]
        bill_analysis["bill_type"] = bill_data["bill_type"]
        # Use conversion dict for chambers
        bill_types_to_chamber = {
            "hjres": "house",
            "hr": "house",
            "s": "senate",
            "sjres": "senate",
        }
        bill_analysis["chamber"] = bill_types_to_chamber.get(bill_data["bill_type"])

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

        # Add delay (if specified)
        if delay > 0:
            print(f"Sleeping for {delay} seconds...")
            time.sleep(delay)

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
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between bill analysis requests (default: 0)",
    )
    args = parser.parse_args()

    generated_bills = generate_bill_analyses(args.force, args.numOfBills, args.delay)
    print(f"Total bill analyses generated: {len(generated_bills)}")
