import argparse
import itertools
import time

import analysis.axis_analysis_client as axis_client
import analysis.batch_runner as batch_runner
import analysis.bill_analysis_client as bill_analysis_client
from analysis.llm_common import is_truncated
import db.db_utils as db_utils
from generate_bill_analysis import MODEL

# Axis scoring is a separate pass over bills that already have a category analysis,
# so category analyses never need to be regenerated when axes change.
INPUT_COLLECTION = "bill_analyses"
OUTPUT_COLLECTION = "bill_axis_analyses"


def get_category_tags(bill_analysis):
    """Names of every category and subcategory assigned to a bill."""
    categories = bill_analysis.get("political_categories", {})
    return [
        c["name"]
        for field in ("primary_categories", "subcategories")
        for c in categories.get(field, [])
    ]


def get_existing_bill_ids():
    """Bill ids that already have an axis analysis for this model and axis schema."""
    return set(
        db_utils.get_collection(OUTPUT_COLLECTION).distinct(
            "bill_id",
            {"model": MODEL, "axis_schema_version": axis_client.AXIS_SCHEMA_VERSION},
        )
    )


def iter_bills_to_analyze(force=False, bill_ids=None, skip_ids=frozenset()):
    """
    Yield each bill that needs an axis analysis, as a dict of the analysis inputs
    ("bill_id", "bill_text", "category_tags") plus its "bill_analysis" (category analysis).
    Only bills with a category analysis for this model and schema version qualify.
    """
    query = {"model": MODEL, "schema_version": bill_analysis_client.SCHEMA_VERSION}
    # Optionally restrict to specific bills (e.g. a pilot set)
    if bill_ids:
        query["bill_id"] = {"$in": bill_ids}

    print("\nFetching category analyses from MongoDB bill_analyses collection...")
    existing_ids = set() if force else get_existing_bill_ids()
    bill_data_coll = db_utils.get_collection("bill_data")

    for bill_analysis in db_utils.get_collection(INPUT_COLLECTION).find(query):
        bill_id = bill_analysis["bill_id"]
        if bill_id in existing_ids or bill_id in skip_ids:
            continue

        bill_data = bill_data_coll.find_one({"bill_id": bill_id})
        summary_text = ((bill_data or {}).get("summary") or {}).get("text")
        if not summary_text:
            print(f"ERROR: Couldn't find summary text for {bill_id}")
            continue

        yield {
            "bill_id": bill_id,
            "bill_text": summary_text,
            "category_tags": get_category_tags(bill_analysis),
            "bill_analysis": bill_analysis,
        }


def load_items(bill_ids):
    """The analysis inputs for specific bills, regardless of existing axis analyses."""
    return list(iter_bills_to_analyze(force=True, bill_ids=bill_ids))


def select_items(force, bill_ids, limit, skip_ids):
    return itertools.islice(iter_bills_to_analyze(force, bill_ids, skip_ids), limit)


def analyze_item(item):
    """Synchronously analyze one bill from iter_bills_to_analyze."""
    return axis_client.analyze_bill_axes(item["bill_text"], item["category_tags"], MODEL)


def save_analysis(item, axis_analysis):
    """Add the bill's metadata to an axis analysis and upsert it to the db."""
    bill_analysis = item["bill_analysis"]

    axis_analysis.update(
        {
            "bill_id": item["bill_id"],
            "model": MODEL,
            "category_schema_version": bill_analysis["schema_version"],
            "congress": bill_analysis.get("congress"),
            "bill_type": bill_analysis.get("bill_type"),
            "chamber": bill_analysis.get("chamber"),
        }
    )

    key_fields = ["bill_id", "model", "axis_schema_version"]
    db_utils.update_one(OUTPUT_COLLECTION, axis_analysis, key_fields)


def generate_axis_analyses(force=False, num_of_bills=None, delay=0.0, bill_ids=None):
    generated_ids = set()
    start_time = time.perf_counter()

    for item in select_items(force, bill_ids, num_of_bills, frozenset()):
        save_analysis(item, analyze_item(item))

        generated_ids.add(item["bill_id"])
        print(f"{item['bill_id']} axis analysis generated")

        if delay > 0:
            print(f"Sleeping for {delay} seconds...")
            time.sleep(delay)

    end_time = time.perf_counter()
    print(f"Elapsed time: {end_time - start_time} seconds")
    return generated_ids


def build_pass_spec():
    return batch_runner.PassSpec(
        name="axis",
        model=MODEL,
        version=axis_client.AXIS_SCHEMA_VERSION,
        version_field="axis_schema_version",
        output_collection=OUTPUT_COLLECTION,
        build_messages=axis_client.build_messages,
        response_format=axis_client.get_response_format,
        parse_response=axis_client.parse_response,
        finalize=lambda result, item: axis_client.finalize(
            result, is_truncated(item["bill_text"])
        ),
        save=save_analysis,
        load_items=load_items,
        select_items=select_items,
        analyze_sync=analyze_item,
        expected_output_tokens=400,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="Regenerate existing axis analyses"
    )
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
        generated_bills = generate_axis_analyses(
            args.force, args.numOfBills, args.delay, args.bill_ids
        )
        print(f"Total axis analyses generated: {len(generated_bills)}")
