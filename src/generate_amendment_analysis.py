"""
Score amendments with an LLM (kind, primary categories, axes) into `amendment_analyses`.

Reads the `amendments` collection built by get_amendments.py. Mirrors
generate_axis_analysis.py, including batch mode through analysis/batch_runner.py.

Usage:
    python3 src/generate_amendment_analysis.py --bill_ids hamdt851-118 hamdt852-118
    python3 src/generate_amendment_analysis.py --mode batch submit --dry_run
"""

import argparse
import itertools
import time

import analysis.amendment_analysis_client as amd_client
import analysis.batch_runner as batch_runner
import analysis.bill_analysis_client as bill_analysis_client
import db.db_utils as db_utils
from generate_axis_analysis import get_category_tags
from generate_bill_analysis import MODEL

INPUT_COLLECTION = "amendments"
OUTPUT_COLLECTION = "amendment_analyses"


def get_existing_ids():
    """Amendment ids that already have an analysis for this model and schema version."""
    return set(
        db_utils.get_collection(OUTPUT_COLLECTION).distinct(
            "amendment_id",
            {"model": MODEL, "amendment_schema_version": amd_client.AMENDMENT_SCHEMA_VERSION},
        )
    )


def amendment_text(amendment):
    """(text, input_source): the purpose, falling back to the description."""
    if (amendment.get("purpose") or "").strip():
        return amendment["purpose"], "purpose"
    return amendment.get("description") or "", "description"


def iter_amendments_to_analyze(force=False, ids=None, skip_ids=frozenset()):
    """
    Yield each amendment that needs an analysis, as a dict of the analysis inputs.

    Only amendments with at least one agree-to (+1) roll call and a parent bill summary
    qualify. "bill_id" is the amendment_id: batch_runner uses it as an opaque id.
    """
    # Tabling votes are kept out of profiles for now, so don't pay to score them
    query = {"votes.vote_direction": 1}
    if ids:
        query["amendment_id"] = {"$in": ids}

    print("\nFetching amendments from MongoDB amendments collection...")
    existing_ids = set() if force else get_existing_ids()
    bill_data_coll = db_utils.get_collection("bill_data")
    bill_analyses_coll = db_utils.get_collection("bill_analyses")
    missing = {"summary": 0, "text": 0}

    for amendment in db_utils.get_collection(INPUT_COLLECTION).find(query).sort("amendment_id"):
        amendment_id = amendment["amendment_id"]
        if amendment_id in existing_ids or amendment_id in skip_ids:
            continue

        text, input_source = amendment_text(amendment)
        if not text.strip():
            missing["text"] += 1
            print(f"ERROR: {amendment_id} has no purpose or description")
            continue

        bill_id = amendment["amended_bill_id"]
        bill_data = bill_data_coll.find_one(
            {"bill_id": bill_id},
            {"summary.text": 1, "short_title": 1, "official_title": 1, "bill_type": 1},
        ) or {}
        summary_text = (bill_data.get("summary") or {}).get("text")
        if not summary_text:
            missing["summary"] += 1
            print(f"ERROR: Couldn't find summary text for {bill_id} (parent of {amendment_id})")
            continue

        bill_analysis = bill_analyses_coll.find_one(
            {
                "bill_id": bill_id,
                "model": MODEL,
                "schema_version": bill_analysis_client.SCHEMA_VERSION,
            },
            {"political_categories": 1},
        )

        yield {
            "bill_id": amendment_id,
            "amendment": amendment,
            "amendment_text": text,
            "input_source": input_source,
            "amended_amendment_purpose": amendment.get("amended_amendment_purpose"),
            "bill_title": bill_data.get("short_title") or bill_data.get("official_title"),
            "bill_type": bill_data.get("bill_type") or bill_id.split("-")[0].rstrip("0123456789"),
            "bill_text": summary_text,
            "category_tags": get_category_tags(bill_analysis) if bill_analysis else [],
        }

    if any(missing.values()):
        print(f"Skipped: {missing['summary']} without a parent summary, {missing['text']} without text")


def load_items(ids):
    """The analysis inputs for specific amendments, regardless of existing analyses."""
    return list(iter_amendments_to_analyze(force=True, ids=ids))


def select_items(force, ids, limit, skip_ids):
    return itertools.islice(iter_amendments_to_analyze(force, ids, skip_ids), limit)


def analyze_item(item):
    """Synchronously analyze one amendment from iter_amendments_to_analyze."""
    return amd_client.analyze_amendment(item, MODEL)


def save_analysis(item, analysis):
    """Add the amendment's metadata to an analysis and upsert it to the db."""
    amendment = item["amendment"]
    analysis.update(
        {
            "amendment_id": amendment["amendment_id"],
            "model": MODEL,
            "congress": amendment["congress"],
            # Same values as bill_analyses ("house"/"senate"), so the same filters work
            "chamber": "house" if amendment["chamber"] == "h" else "senate",
            # The parent bill's type, so --bill_type filters amendments with their bills
            "bill_type": item["bill_type"],
            "amended_bill_id": amendment["amended_bill_id"],
            "amended_amendment_id": amendment.get("amended_amendment_id"),
            "second_degree": bool(amendment.get("amended_amendment_id")),
        }
    )
    key_fields = ["amendment_id", "model", "amendment_schema_version"]
    db_utils.update_one(OUTPUT_COLLECTION, analysis, key_fields)


def generate_amendment_analyses(force=False, num_of_bills=None, delay=0.0, ids=None):
    generated_ids = set()
    start_time = time.perf_counter()

    for item in select_items(force, ids, num_of_bills, frozenset()):
        try:
            save_analysis(item, analyze_item(item))
        except Exception as e:
            print(f"{item['bill_id']} FAILED: {e}")
            continue

        generated_ids.add(item["bill_id"])
        print(f"{item['bill_id']} amendment analysis generated")

        if delay > 0:
            print(f"Sleeping for {delay} seconds...")
            time.sleep(delay)

    print(f"Elapsed time: {time.perf_counter() - start_time} seconds")
    return generated_ids


def build_pass_spec():
    return batch_runner.PassSpec(
        name="amd",
        model=MODEL,
        version=amd_client.AMENDMENT_SCHEMA_VERSION,
        version_field="amendment_schema_version",
        output_collection=OUTPUT_COLLECTION,
        build_messages=amd_client.build_messages,
        response_format=amd_client.get_response_format,
        parse_response=amd_client.parse_response,
        finalize=amd_client.finalize,
        save=save_analysis,
        load_items=load_items,
        select_items=select_items,
        analyze_sync=analyze_item,
        expected_output_tokens=500,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate existing amendment analyses"
    )
    parser.add_argument(
        "--numOfBills", type=int, help="Number of amendments to process"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between requests (default: 0)",
    )
    parser.add_argument(
        "--bill_ids",
        nargs="+",
        help="Only analyze these AMENDMENT ids (e.g. hamdt858-118 samdt1834-118), "
        "not parent bill ids. Named --bill_ids to match the other generators.",
    )
    batch_runner.add_batch_arguments(parser)
    args = parser.parse_args()

    db_utils.ensure_indexes()
    if args.mode == "batch":
        batch_runner.run_batch_action(build_pass_spec(), args)
    else:
        generated = generate_amendment_analyses(
            args.force, args.numOfBills, args.delay, args.bill_ids
        )
        print(f"Total amendment analyses generated: {len(generated)}")
