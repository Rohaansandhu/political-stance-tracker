import argparse
import time

import analysis.axis_analysis_client as axis_client
import analysis.bill_analysis_client as bill_analysis_client
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


def generate_axis_analyses(force=False, num_of_bills=None, delay=0.0):
    generated_ids = set()
    start_time = time.perf_counter()

    existing_ids = set() if force else get_existing_bill_ids()
    bill_data_coll = db_utils.get_collection("bill_data")

    print("\nFetching category analyses from MongoDB bill_analyses collection...")
    category_analyses = db_utils.get_collection(INPUT_COLLECTION).find(
        {"model": MODEL, "schema_version": bill_analysis_client.SCHEMA_VERSION}
    )

    for bill_analysis in category_analyses:
        bill_id = bill_analysis["bill_id"]

        if bill_id in existing_ids or bill_id in generated_ids:
            continue

        bill_data = bill_data_coll.find_one({"bill_id": bill_id})
        summary_text = ((bill_data or {}).get("summary") or {}).get("text")
        if not summary_text:
            print(f"ERROR: Couldn't find summary text for {bill_id}")
            continue

        axis_analysis = axis_client.analyze_bill_axes(
            summary_text, get_category_tags(bill_analysis), MODEL
        )

        axis_analysis.update(
            {
                "bill_id": bill_id,
                "model": MODEL,
                "category_schema_version": bill_analysis["schema_version"],
                "congress": bill_analysis.get("congress"),
                "bill_type": bill_analysis.get("bill_type"),
                "chamber": bill_analysis.get("chamber"),
            }
        )

        key_fields = ["bill_id", "model", "axis_schema_version"]
        db_utils.update_one(OUTPUT_COLLECTION, axis_analysis, key_fields)

        generated_ids.add(bill_id)
        print(f"{bill_id} axis analysis generated")

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
    args = parser.parse_args()

    generated_bills = generate_axis_analyses(args.force, args.numOfBills, args.delay)
    print(f"Total axis analyses generated: {len(generated_bills)}")
