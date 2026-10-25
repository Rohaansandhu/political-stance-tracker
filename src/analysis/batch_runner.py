"""
OpenAI Batch API runner for the bill analysis passes (category and axis).

Requests go out in batches and results come back hours later, so the work is split into
four resumable commands, with every job recorded in the `llm_batches` Mongo collection:

  submit   build one request per bill, chunk, upload, and create batches
  status   poll open batches
  cancel   cancel open batches (finished requests still come back on collect)
  collect  download finished batches, parse/validate, and upsert the analyses
  retry    resubmit only the failures (with the corrective retry prompts); after
           MAX_BATCH_ROUNDS rounds, run what's left through the synchronous path

A pass (category or axis) is described by a PassSpec, so both use the same runner.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from analysis import llm_common
import db.db_utils as db_utils

try:
    import tiktoken
except ImportError:  # token counts fall back to ~4 characters per token
    tiktoken = None

BATCH_COLLECTION = "llm_batches"
BATCH_INPUT_DIR = Path("data/batch_inputs")

ENDPOINT = "/v1/chat/completions"
COMPLETION_WINDOW = "24h"

# Start conservative; raise after seeing the account's enqueued-token limit
DEFAULT_CHUNK_SIZE = 500
# Hard limits per batch file are 50,000 requests and 200 MB
MAX_REQUESTS_PER_FILE = 50_000
MAX_BYTES_PER_FILE = 190 * 1024 * 1024

# Retry batches before falling back to the synchronous path
MAX_BATCH_ROUNDS = 2

OPEN_STATUSES = {"validating", "in_progress", "finalizing", "cancelling"}
# Partial results of expired/cancelled batches still come back
COLLECTABLE_STATUSES = {"completed", "expired", "cancelled", "failed"}

DEFAULT_MAX_COST = 5.0
# Batch requests are billed at this fraction of the normal price
BATCH_DISCOUNT = 0.5
# Per-request output cap, in completion tokens (includes reasoning tokens for reasoning models)
DEFAULT_MAX_COMPLETION_TOKENS = 16000


@dataclass
class PassSpec:
    """Everything the runner needs to know about one analysis pass."""

    name: str  # short pass id used in custom_ids and llm_batches, e.g. "cat"
    model: str
    version: int  # schema version of the analyses this pass produces
    version_field: str  # name of that version field on saved analyses
    output_collection: str
    build_messages: Callable  # (item, use_reduced, retry_context) -> messages
    response_format: Callable  # () -> strict JSON schema response_format, or None
    parse_response: Callable  # text -> (result, error_type, bad_names)
    finalize: Callable  # (result, item) -> analysis dict to save
    save: Callable  # (item, analysis) -> None, upserts to the db
    load_items: Callable  # (bill_ids) -> items, regardless of existing analyses
    select_items: Callable  # (force, bill_ids, limit, skip_ids) -> iterable of new items
    analyze_sync: Callable  # (item) -> analysis dict, the synchronous fallback
    expected_output_tokens: int  # per bill, for cost estimates


def add_batch_arguments(parser):
    """Add the --mode and batch options shared by the generate_*_analysis scripts."""
    parser.add_argument(
        "--mode",
        choices=["sync", "batch"],
        default="sync",
        help="sync (default, any provider) or batch (OpenAI Batch API, needs CLIENT=openai)",
    )
    parser.add_argument(
        "batch_action",
        nargs="?",
        choices=["submit", "status", "cancel", "collect", "retry"],
        help="With --mode batch: which step to run",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Batch submit/retry: count tokens and print the estimated cost without submitting",
    )
    parser.add_argument(
        "--max_cost",
        type=float,
        default=DEFAULT_MAX_COST,
        help=f"Batch: estimated cost above which --confirm is required (default: ${DEFAULT_MAX_COST})",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Batch: allow a submission over --max_cost, or with an unknown cost",
    )
    parser.add_argument(
        "--chunk_size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Batch: requests per batch (default: {DEFAULT_CHUNK_SIZE})",
    )


# --- requests ---


def make_custom_id(bill_id, pass_name, attempt):
    return f"{bill_id}|{pass_name}|{attempt}"


def parse_custom_id(custom_id):
    """'hr7888-118|cat|0' -> ('hr7888-118', 'cat', 0)"""
    bill_id, pass_name, attempt = custom_id.split("|")
    return bill_id, pass_name, int(attempt)


def request_params():
    """Per-request settings from the environment (the model's API must support them)."""
    params = {
        "max_completion_tokens": int(
            os.getenv("LLM_MAX_COMPLETION_TOKENS", DEFAULT_MAX_COMPLETION_TOKENS)
        )
    }
    if os.getenv("LLM_REASONING_EFFORT"):
        params["reasoning_effort"] = os.getenv("LLM_REASONING_EFFORT")
    return params


def build_request(spec, item, attempt, retry_context=None, use_reduced=False):
    """One JSONL line of a batch input file."""
    body = {
        "model": spec.model,
        "messages": spec.build_messages(item, use_reduced, retry_context),
        **request_params(),
    }
    response_format = spec.response_format()
    if response_format:
        body["response_format"] = response_format

    return {
        "custom_id": make_custom_id(item["bill_id"], spec.name, attempt),
        "method": "POST",
        "url": ENDPOINT,
        "body": body,
    }


# --- cost estimates ---


def count_tokens(messages, model):
    """Approximate prompt tokens of a request (tiktoken if available, else ~4 chars/token)."""
    text_chunks = [m["content"] for m in messages]
    if tiktoken is None:
        return sum(len(t) for t in text_chunks) // 4

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("o200k_base")
    # ~4 tokens of framing per message
    return sum(len(encoding.encode(t)) + 4 for t in text_chunks)


def get_prices():
    """$/1M-token prices from the environment: (input, cached input, output), None if unset."""

    def price(name):
        return float(os.environ[name]) if os.getenv(name) else None

    return (
        price("LLM_PRICE_INPUT_PER_M"),
        price("LLM_PRICE_CACHED_INPUT_PER_M"),
        price("LLM_PRICE_OUTPUT_PER_M"),
    )


def estimate_cost(input_tokens, cached_tokens, output_tokens):
    """Batch cost in dollars, or None when input/output prices aren't configured."""
    price_in, price_cached, price_out = get_prices()
    if price_in is None or price_out is None:
        return None

    uncached = input_tokens - cached_tokens
    cost = (
        uncached * price_in
        + cached_tokens * (price_cached if price_cached is not None else price_in)
        + output_tokens * price_out
    )
    return cost / 1e6 * BATCH_DISCOUNT


def format_cost(cost):
    return "unknown (set LLM_PRICE_INPUT_PER_M and LLM_PRICE_OUTPUT_PER_M)" if cost is None else f"${cost:,.2f}"


def print_estimate(spec, requests):
    """Print the token/cost estimate for a set of requests. Returns the high estimate."""
    messages = [r["body"]["messages"] for r in requests]
    input_tokens = sum(count_tokens(m, spec.model) for m in messages)
    # The system message is the shared prefix; after the first request it can be cached
    system_tokens = count_tokens(messages[0][:1], spec.model)
    cacheable = system_tokens * (len(requests) - 1)
    reasoning = int(os.getenv("LLM_EXPECTED_REASONING_TOKENS", 0))
    output_tokens = len(requests) * (spec.expected_output_tokens + reasoning)

    high = estimate_cost(input_tokens, 0, output_tokens)
    low = estimate_cost(input_tokens, cacheable, output_tokens)
    print(f"\nRequests:             {len(requests)}")
    print(f"Input tokens:         ~{input_tokens:,} (~{cacheable:,} cacheable)")
    print(
        f"Output tokens:        ~{output_tokens:,} "
        f"(assumes {spec.expected_output_tokens} + {reasoning} reasoning per bill)"
    )
    print(f"Estimated batch cost: {format_cost(low)} with caching, {format_cost(high)} without")
    return high


# --- batch bookkeeping ---


def batches_collection():
    return db_utils.get_collection(BATCH_COLLECTION)


def pass_filter(spec):
    return {"pass": spec.name, "model": spec.model, "version": spec.version}


def blocked_bill_ids(spec):
    """Bills that must not be submitted again: in an uncollected batch, or awaiting retry."""
    blocked = set()
    for doc in batches_collection().find(pass_filter(spec)):
        if not doc.get("collected"):
            blocked.update(doc["bill_ids"])
        blocked.update(f["bill_id"] for f in doc.get("failures", []) if not f["retried"])
    return blocked


def chunk_requests(requests, chunk_size):
    """Split requests into batches under the request-count and file-size limits."""
    chunk_size = min(chunk_size, MAX_REQUESTS_PER_FILE)
    chunks, current, current_bytes = [], [], 0
    for request in requests:
        size = len(json.dumps(request)) + 1
        if current and (len(current) >= chunk_size or current_bytes + size > MAX_BYTES_PER_FILE):
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(request)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def submit_requests(spec, requests, attempt, args):
    """
    Estimate, gate, and submit requests as one or more batches.
    Returns the bill ids submitted ([] for a dry run or a blocked submission).
    """
    if not requests:
        print("Nothing to submit.")
        return []

    cost = print_estimate(spec, requests)
    if args.dry_run:
        print("\nDry run: nothing submitted.")
        return []

    if not args.confirm and (cost is None or cost > args.max_cost):
        print(
            f"\nNot submitting: estimated cost is {format_cost(cost)} (limit ${args.max_cost:,.2f}). "
            "Re-run with --confirm to submit anyway."
        )
        return []

    BATCH_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    submitted = []

    for number, chunk in enumerate(chunk_requests(requests, args.chunk_size)):
        path = BATCH_INPUT_DIR / f"{spec.name}_{spec.version}_a{attempt}_{stamp}_{number}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in chunk))

        with open(path, "rb") as f:
            input_file = llm_common.client.files.create(file=f, purpose="batch")
        batch = llm_common.client.batches.create(
            input_file_id=input_file.id,
            endpoint=ENDPOINT,
            completion_window=COMPLETION_WINDOW,
            metadata={"pass": spec.name, "attempt": str(attempt)},
        )

        bill_ids = [parse_custom_id(r["custom_id"])[0] for r in chunk]
        batches_collection().insert_one(
            {
                "batch_id": batch.id,
                "input_file_id": input_file.id,
                **pass_filter(spec),
                "attempt": attempt,
                "bill_ids": bill_ids,
                "status": batch.status,
                "submitted_at": datetime.now(timezone.utc),
                "collected": False,
                "failures": [],
            }
        )
        submitted.extend(bill_ids)
        print(f"Submitted {batch.id}: {len(chunk)} requests ({batch.status})")

    return submitted


# --- commands ---


def submit(spec, args):
    items = list(
        spec.select_items(args.force, args.bill_ids, args.numOfBills, blocked_bill_ids(spec))
    )
    print(f"{len(items)} bills to submit")
    requests = [build_request(spec, item, attempt=0) for item in items]
    submit_requests(spec, requests, 0, args)


def refresh_batches(spec):
    """Poll every uncollected batch of this pass and store its latest status."""
    docs = list(batches_collection().find({**pass_filter(spec), "collected": False}))
    for doc in docs:
        batch = llm_common.client.batches.retrieve(doc["batch_id"])
        counts = batch.request_counts
        update = {
            "status": batch.status,
            "output_file_id": batch.output_file_id,
            "error_file_id": batch.error_file_id,
            "request_counts": (
                {"total": counts.total, "completed": counts.completed, "failed": counts.failed}
                if counts
                else None
            ),
        }
        batches_collection().update_one({"batch_id": doc["batch_id"]}, {"$set": update})
        doc.update(update)
    return docs


def status(spec, args):
    docs = refresh_batches(spec)
    if not docs:
        print("No open batches.")
    for doc in docs:
        print(f"{doc['batch_id']}  attempt {doc['attempt']}  {doc['status']}  {doc['request_counts']}")


def cancel(spec, args):
    """Cancel every uncollected batch of this pass. Collect them once they report cancelled."""
    docs = list(batches_collection().find({**pass_filter(spec), "collected": False}))
    if not docs:
        print("No open batches.")
    for doc in docs:
        batch = llm_common.client.batches.cancel(doc["batch_id"])
        batches_collection().update_one({"batch_id": doc["batch_id"]}, {"$set": {"status": batch.status}})
        print(f"{doc['batch_id']}: {batch.status}")


def read_jsonl(file_id):
    if not file_id:
        return []
    text = llm_common.client.files.content(file_id).text
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def interpret_line(line):
    """
    Pull the response text and token usage out of one output/error line.
    Returns (text, usage, error_type, detail); error_type is None when there is text to parse.
    """
    error = line.get("error")
    response = line.get("response")
    if response is None:
        # e.g. batch_expired: the request never ran
        return None, None, "no_response", (error or {}).get("message", "no response")

    body = response.get("body") or {}
    if response.get("status_code") != 200:
        api_error = body.get("error") or error or {}
        error_type = "context_length" if api_error.get("code") == "context_length_exceeded" else "api_error"
        return None, None, error_type, api_error.get("message", f"HTTP {response.get('status_code')}")

    usage_body = body.get("usage") or {}
    usage = {
        "input": usage_body.get("prompt_tokens", 0),
        "cached": (usage_body.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        "output": usage_body.get("completion_tokens", 0),
        "reasoning": (usage_body.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
    }

    choice = body["choices"][0]
    message = choice["message"]
    if message.get("refusal"):
        return None, usage, "refusal", message["refusal"]
    if choice.get("finish_reason") == "length":
        return None, usage, "length", "output hit the token limit"
    return message.get("content") or "", usage, None, None


def collect_batch(spec, doc):
    """Process one finished batch: save good analyses, record failures and token usage."""
    items = {item["bill_id"]: item for item in spec.load_items(doc["bill_ids"])}
    lines = read_jsonl(doc.get("output_file_id")) + read_jsonl(doc.get("error_file_id"))

    saved, seen, failures, usage_by_bill = 0, set(), [], {}
    for line in lines:
        bill_id, _, attempt = parse_custom_id(line["custom_id"])
        if bill_id in seen:
            continue
        seen.add(bill_id)

        text, usage, error_type, detail = interpret_line(line)
        if usage:
            usage_by_bill[bill_id] = usage

        bad_names = []
        if error_type is None:
            result, error_type, bad_names = spec.parse_response(text)
            if error_type is None:
                item = items.get(bill_id)
                if item is not None:
                    spec.save(item, spec.finalize(result, item))
                    saved += 1
                    continue
                error_type, detail = "missing_item", "bill no longer available"

        failures.append(
            {
                "bill_id": bill_id,
                "attempt": attempt,
                "error_type": error_type,
                "detail": detail,
                "previous_response": (text or "")[:500],
                "bad_names": bad_names,
                "retried": False,
            }
        )

    # Requests absent from both files (e.g. a failed batch) never got a response
    for bill_id in doc["bill_ids"]:
        if bill_id not in seen:
            failures.append(
                {
                    "bill_id": bill_id,
                    "attempt": doc["attempt"],
                    "error_type": "no_response",
                    "detail": f"batch {doc['status']}",
                    "previous_response": "",
                    "bad_names": [],
                    "retried": False,
                }
            )

    totals = {k: sum(u[k] for u in usage_by_bill.values()) for k in ("input", "cached", "output", "reasoning")}
    batches_collection().update_one(
        {"batch_id": doc["batch_id"]},
        {
            "$set": {
                "collected": True,
                "collected_at": datetime.now(timezone.utc),
                "saved": saved,
                "failures": failures,
                "usage_by_bill": usage_by_bill,
                "usage_total": totals,
            }
        },
    )

    cost = estimate_cost(totals["input"], totals["cached"], totals["output"])
    print(
        f"{doc['batch_id']}: saved {saved}, failed {len(failures)}; tokens in {totals['input']:,} "
        f"({totals['cached']:,} cached), out {totals['output']:,} ({totals['reasoning']:,} reasoning); "
        f"cost {format_cost(cost)}"
    )
    return failures


def collect(spec, args):
    docs = refresh_batches(spec)
    ready = [d for d in docs if d["status"] in COLLECTABLE_STATUSES]
    for doc in docs:
        if doc["status"] not in COLLECTABLE_STATUSES:
            print(f"{doc['batch_id']}: still {doc['status']}, skipping")
    for doc in ready:
        collect_batch(spec, doc)
    if ready:
        print("\nRun the retry command to resubmit any failures.")


def pending_failures(spec):
    """(batch doc, failure) for every collected failure that hasn't been retried yet."""
    pending = []
    for doc in batches_collection().find({**pass_filter(spec), "collected": True}):
        pending.extend((doc, f) for f in doc.get("failures", []) if not f["retried"])
    return pending


def mark_retried(pending, bill_ids, **extra):
    """Flag failures for these bills as retried, so they aren't picked up again."""
    for doc in {d["batch_id"]: d for d, _ in pending}.values():
        for failure in doc["failures"]:
            if failure["bill_id"] in bill_ids and not failure["retried"]:
                failure["retried"] = True
                failure.update(extra)
        batches_collection().update_one(
            {"batch_id": doc["batch_id"]}, {"$set": {"failures": doc["failures"]}}
        )


def retry(spec, args):
    open_ids = {
        bill_id
        for doc in batches_collection().find({**pass_filter(spec), "collected": False})
        for bill_id in doc["bill_ids"]
    }
    pending = [(d, f) for d, f in pending_failures(spec) if f["bill_id"] not in open_ids]
    if not pending:
        print("No failures to retry.")
        return

    batch_round = [(d, f) for d, f in pending if f["attempt"] < MAX_BATCH_ROUNDS]
    fallback = [(d, f) for d, f in pending if f["attempt"] >= MAX_BATCH_ROUNDS]

    items = {item["bill_id"]: item for item in spec.load_items([f["bill_id"] for _, f in pending])}

    requests = []
    for _, failure in batch_round:
        item = items.get(failure["bill_id"])
        if item is None:
            continue
        retry_context = {
            "error_type": failure["error_type"],
            "previous_response": failure["previous_response"],
            "bad_names": failure["bad_names"],
        }
        requests.append(
            build_request(
                spec,
                item,
                failure["attempt"] + 1,
                retry_context,
                use_reduced=failure["error_type"] == "context_length",
            )
        )

    print(f"{len(requests)} failures to resubmit as a batch, {len(fallback)} for the sync fallback")
    # One set of batches per attempt number, so each batch's custom_ids carry its own attempt
    submitted = set()
    for attempt in sorted({parse_custom_id(r["custom_id"])[2] for r in requests}):
        group = [r for r in requests if parse_custom_id(r["custom_id"])[2] == attempt]
        submitted.update(submit_requests(spec, group, attempt, args))
    mark_retried(batch_round, submitted)

    if fallback and not args.dry_run:
        run_sync_fallback(spec, fallback, items)


def run_sync_fallback(spec, fallback, items):
    """Last resort: run failures that exhausted their batch rounds through the sync path."""
    for _, failure in fallback:
        bill_id = failure["bill_id"]
        item = items.get(bill_id)
        if item is None:
            continue
        try:
            spec.save(item, spec.analyze_sync(item))
            print(f"{bill_id}: saved via sync fallback")
            mark_retried(fallback, {bill_id}, fallback_saved=True)
        except Exception as e:
            print(f"{bill_id}: sync fallback failed, needs review: {e}")
            mark_retried(fallback, {bill_id}, fallback_saved=False, fallback_error=str(e))


def run_batch_action(spec, args):
    """Dispatch `--mode batch <action>`."""
    if llm_common.CLIENT != "openai":
        raise SystemExit(f"Batch mode only works with CLIENT=openai (got {llm_common.CLIENT!r}).")
    if not args.batch_action:
        raise SystemExit("Specify a batch action: submit, status, cancel, collect, or retry.")

    {"submit": submit, "status": status, "cancel": cancel, "collect": collect, "retry": retry}[
        args.batch_action
    ](spec, args)
