import json
from functools import lru_cache

from analysis.definitions import llm_axes
from analysis.llm_common import (
    STRUCTURED_OUTPUTS,
    analyze_with_retries,
    clean_json_response,
    is_truncated,
    truncate_bill_text,
)
from schema.bill_analyses import AxisAnalysis, axis_response_format

# Update after every change to the axis prompt or political_axes.json.
# Separate from the category SCHEMA_VERSION so axes can be re-run without redoing categories.
AXIS_SCHEMA_VERSION = 1

SYSTEM_PROMPT = """
You are an expert political analyst specializing in legislative classification.

Your task is to score a bill on a fixed set of ideological axes. Each axis has two poles,
and you determine which way a YES vote on the bill pushes along that axis.

Rules:
- Score each axis independently of the others.
- Omit any axis the bill does not meaningfully engage. Do not guess.
- Apply the boundary rules for each axis exactly as written. They say which axis a given
  kind of provision belongs to when axes overlap.
- Judge only the content of the bill. Never infer a score from its sponsor or party.
- Be objective and consider multiple political perspectives.
"""

# Static instructions come first in the user message, with the bill last, so everything
# before the category tags is an identical prefix across bills (OpenAI caches shared prefixes)
USER_INSTRUCTIONS = """Please score the following bill on the ideological axes defined in the system message.

For each relevant axis provide:
- position: -1.0 to 1.0, the direction a YES vote pushes along the axis (0 = neutral or mixed)
- relevance: 0.0 to 1.0, how central the axis is to the bill (1.0 = the main point of the bill)
- reasoning: one or two sentences

Output Format (STRICT JSON)
{
    "axis_scores": [{
        "axis": "axis id exactly as given in the system message",
        "position": -1.0 to 1.0,
        "relevance": 0.0 to 1.0,
        "reasoning": "string"
    }]
}

CRITICAL: Return ONLY valid JSON. No markdown, no explanation, no text outside the JSON object."""


def format_axis(axis):
    """Render one axis definition as prompt text."""
    neg, pos = axis["negative_pole"], axis["positive_pole"]
    typical = axis["typical_categories"]

    lines = [
        f'AXIS "{axis["id"]}" ({axis["name"]})',
        f'  -1 = {neg["label"]}: {neg["description"]}',
        f'  +1 = {pos["label"]}: {pos["description"]}',
        f'  Scoring rule: {axis["scoring_rule"]}',
    ]
    for rule in axis["boundary_rules"]:
        lines.append(f"  Boundary rule: {rule}")
    lines.append(
        f'  Usually relevant to: {", ".join(typical["most"])}. '
        f'Sometimes: {", ".join(typical["some"])}. '
        "(A hint only: score any axis the bill genuinely engages.)"
    )
    for example in axis["anchor_examples"]:
        lines.append(f'  Example: {example["description"]} -> {example["position"]}')
    return "\n".join(lines)


def normalize_axis_scores(axis_scores, valid_ids):
    """
    Clamp ranges and drop duplicate axes (first one wins).
    Returns (scores, bad_ids) where bad_ids are axis ids that are unknown or not LLM-scored.
    """
    scores = []
    seen = set()
    bad_ids = []

    for score in axis_scores:
        axis_id = score["axis"]
        if axis_id not in valid_ids:
            bad_ids.append(axis_id)
            continue
        if axis_id in seen:
            continue
        seen.add(axis_id)

        scores.append(
            {
                **score,
                "position": max(-1.0, min(1.0, score["position"])),
                "relevance": max(0.0, min(1.0, score["relevance"])),
            }
        )

    return scores, bad_ids


@lru_cache(maxsize=None)
def build_system_message(use_reduced=False):
    """
    System message with the axis definitions. Byte-identical for every bill (cached),
    so providers can cache it as a shared prompt prefix. There is no reduced variant.
    """
    axis_text = "\n\n".join(format_axis(a) for a in llm_axes())
    return f"{SYSTEM_PROMPT}\nAXES\n{axis_text}"


def build_retry_note(retry_context):
    """Corrective text appended to the user message after a failed attempt."""
    error_type = retry_context["error_type"]

    if error_type == "bad_axis":
        valid_ids = ", ".join(sorted(a["id"] for a in llm_axes()))
        return f"""

RETRY ATTEMPT: Your previous response used axis ids that do not exist: {", ".join(retry_context["bad_names"])}.
Use only these axis ids, spelled exactly: {valid_ids}."""

    if error_type == "bad_format":
        return """

RETRY ATTEMPT: Your previous response had invalid JSON format and/or did not have the required fields.
Return ONLY valid JSON in exactly the format specified."""

    # Response was cut off by the output limit
    if error_type == "length":
        return """

RETRY ATTEMPT: Your previous response was cut off before it finished.
Keep every "reasoning" string to one sentence and return complete, valid JSON."""

    return ""


def build_messages(bill, use_reduced=False, retry_context=None):
    """
    Chat messages for scoring one bill. `use_reduced` is accepted for compatibility with
    the category client, but axes have no reduced prompt.

    Args:
        bill (dict): {"bill_text", "category_tags"}
        retry_context (dict): {"error_type", "previous_response", "bad_names"} after a failure
    """
    bill_text, _ = truncate_bill_text(bill["bill_text"])
    tags_text = ", ".join(bill["category_tags"]) if bill["category_tags"] else "(none)"

    user_message = f"""{USER_INSTRUCTIONS}

CATEGORY TAGS ALREADY ASSIGNED TO THIS BILL (context only):
{tags_text}

BILL TEXT:
{bill_text}"""
    if retry_context:
        user_message += build_retry_note(retry_context)

    return [
        {"role": "system", "content": build_system_message()},
        {"role": "user", "content": user_message},
    ]


@lru_cache(maxsize=None)
def get_response_format():
    """Strict JSON schema for responses, or None when structured outputs aren't in use."""
    if not STRUCTURED_OUTPUTS:
        return None
    return axis_response_format([a["id"] for a in llm_axes()])


def parse_response(text):
    """
    Parse and validate a model response.

    Returns:
        (result, error_type, bad_names): error_type is None on success, otherwise
        "bad_format" or "bad_axis" (with the unknown/computed axis ids).
    """
    try:
        analysis = AxisAnalysis.model_validate(json.loads(clean_json_response(text.strip())))
    except ValueError:
        # JSONDecodeError and pydantic ValidationError are both ValueErrors
        return None, "bad_format", []

    valid_ids = {a["id"] for a in llm_axes()}
    axis_scores, bad_ids = normalize_axis_scores(
        [s.model_dump() for s in analysis.axis_scores], valid_ids
    )
    if bad_ids:
        return None, "bad_axis", bad_ids

    return {"axis_scores": axis_scores}, None, []


def finalize(result, bill_truncated):
    """Add metadata to a validated analysis."""
    result["axis_schema_version"] = AXIS_SCHEMA_VERSION
    result["bill_truncated"] = bill_truncated
    return result


def analyze_bill_axes(bill_text, category_tags, model, max_retries=4):
    """
    Score a bill on every LLM-scored axis (synchronously).
    Batch mode (analysis/batch_runner.py) uses the same build_messages/parse_response/finalize.

    Args:
        bill_text (str): The text (or summary) of the bill
        category_tags (list[str]): Category/subcategory names already assigned to the bill
        model (str): The model to use for analysis
        max_retries (int): Maximum number of retry attempts

    Returns:
        dict: {"axis_scores": [...], "axis_schema_version": int, "bill_truncated": bool}

    Raises:
        Exception: If the response is still invalid after all retries
    """
    if not bill_text.strip():
        raise ValueError("Bill text cannot be empty")

    bill = {"bill_text": bill_text, "category_tags": category_tags}
    bill_truncated = is_truncated(bill_text)

    return analyze_with_retries(
        model,
        lambda use_reduced, retry_context: build_messages(bill, use_reduced, retry_context),
        parse_response,
        lambda result: finalize(result, bill_truncated),
        response_format=get_response_format(),
        max_retries=max_retries,
    )
