import argparse
import itertools
import time
from pathlib import Path
import os
from dotenv import load_dotenv
import analysis.batch_runner as batch_runner
import analysis.bill_analysis_client as bill_analysis_client
from analysis.llm_common import is_truncated
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

OUTPUT_COLLECTION = "bill_analyses"

# Use conversion dict for chambers
BILL_TYPES_TO_CHAMBER = {
    "hjres": "house",
    "hr": "house",
    "s": "senate",
    "sjres": "senate",
}


def get_existing_bill_ids():
    """Bill ids that already have an analysis for this model and schema version."""
    return set(
        db_utils.get_collection(OUTPUT_COLLECTION).distinct(
            "bill_id",
            {"model": MODEL, "schema_version": bill_analysis_client.SCHEMA_VERSION},
        )
    )


def iter_bills_to_analyze(force=False, bill_ids=None, skip_ids=frozenset()):
    """
    Yield each bill that needs a category analysis, as a dict of the analysis inputs
    ("bill_id", "bill_text", "legislative_subjects", "top_subject") plus its "bill_data".
    Skips bills that already have an analysis for this model and schema version
    (unless `force`), bills in `skip_ids`, and bills with no summary.
    """
    query = {"bill_type": {"$in": list(BILL_TYPES_TO_CHAMBER)}}
    # Optionally restrict to specific bills (e.g. a pilot set)
    if bill_ids:
        query["bill_id"] = {"$in": bill_ids}

    print("\nFetching bills from MongoDB bill_data collection...")
    existing_ids = set() if force else get_existing_bill_ids()

    for bill_data in db_utils.get_collection("bill_data").find(query):
        bill_id = bill_data.get("bill_id")
        if bill_id in existing_ids or bill_id in skip_ids:
            continue

        summary_data = bill_data.get("summary")
        if not summary_data:
            print(f"ERROR: Couldn't find summary data for {bill_id}")
            continue
        summary_text = summary_data.get("text")
        if not summary_text:
            print(f"ERROR: Couldn't find summary text for {bill_id}")
            continue

        yield {
            "bill_id": bill_id,
            "bill_text": summary_text,
            # Not required like summaries, but helpful for LLM to contextualize
            "legislative_subjects": bill_data.get("subjects") or [],
            "top_subject": bill_data.get("subjects_top_term"),
            "bill_data": bill_data,
        }


def load_items(bill_ids):
    """The analysis inputs for specific bills, regardless of existing analyses."""
    return list(iter_bills_to_analyze(force=True, bill_ids=bill_ids))


def select_items(force, bill_ids, limit, skip_ids):
    return itertools.islice(iter_bills_to_analyze(force, bill_ids, skip_ids), limit)


def analyze_item(item):
    """Synchronously analyze one bill from iter_bills_to_analyze."""
    return bill_analysis_client.analyze_bill(
        item["bill_text"], item["legislative_subjects"], item["top_subject"], MODEL
    )


def save_analysis(item, analysis):
    """Add the bill's metadata to an analysis and upsert it to the db."""
    bill_data = item["bill_data"]

    analysis["bill_id"] = item["bill_id"]
    analysis["model"] = MODEL
    # Using bill_data, add congress, chamber, and bill type
    analysis["congress"] = bill_data["congress"]
    analysis["bill_type"] = bill_data["bill_type"]
    analysis["chamber"] = BILL_TYPES_TO_CHAMBER.get(bill_data["bill_type"])

    filter = ["bill_id", "model", "schema_version"]
    db_utils.update_one(OUTPUT_COLLECTION, analysis, filter)


def generate_bill_analyses(force=False, num_of_bills=None, delay=0.0, bill_ids=None):
    generated_ids = set()
    start_time = time.perf_counter()

    for item in select_items(force, bill_ids, num_of_bills, frozenset()):
        # Call LLM Client
        analysis = analyze_item(item)
        save_analysis(item, analysis)

        generated_ids.add(item["bill_id"])
        print(f"{item['bill_id']} bill analysis generated")

        # Add delay (if specified)
        if delay > 0:
            print(f"Sleeping for {delay} seconds...")
            time.sleep(delay)

    end_time = time.perf_counter()
    print(f"Elapsed time: {end_time - start_time} seconds")
    return generated_ids


def build_pass_spec():
    return batch_runner.PassSpec(
        name="cat",
        model=MODEL,
        version=bill_analysis_client.SCHEMA_VERSION,
        version_field="schema_version",
        output_collection=OUTPUT_COLLECTION,
        build_messages=bill_analysis_client.build_messages,
        response_format=bill_analysis_client.get_response_format,
        parse_response=bill_analysis_client.parse_response,
        finalize=lambda result, item: bill_analysis_client.finalize(
            result, is_truncated(item["bill_text"])
        ),
        save=save_analysis,
        load_items=load_items,
        select_items=select_items,
        analyze_sync=analyze_item,
        expected_output_tokens=1500,
    )


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
    parser.add_argument(
        "--bill_ids",
        nargs="+",
        help="Only analyze these bill ids (e.g. hr7888-118 sjres49-119)",
    )
    batch_runner.add_batch_arguments(parser)
    args = parser.parse_args()

    if args.mode == "batch":
        batch_runner.run_batch_action(build_pass_spec(), args)
    else:
        generated_bills = generate_bill_analyses(
            args.force, args.numOfBills, args.delay, args.bill_ids
        )
        print(f"Total bill analyses generated: {len(generated_bills)}")
