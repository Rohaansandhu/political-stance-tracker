"""
LLM client for scoring amendments: kind, primary categories, and axes in one call.

Same contract as axis_analysis_client (build_messages, get_response_format, parse_response,
finalize), so generate_amendment_analysis.py can reuse batch_runner unchanged.
"""

import json
from functools import lru_cache

from analysis.axis_analysis_client import format_axis, normalize_axis_scores
from analysis.bill_analysis_client import correct_name
from analysis.definitions import llm_axes, load_categories
from analysis.llm_common import (
    STRUCTURED_OUTPUTS,
    analyze_with_retries,
    clean_json_response,
)
from schema.bill_analyses import (
    AMENDMENT_KINDS,
    AmendmentAnalysis,
    amendment_response_format,
)

# Update after every change to this prompt, the categories, or the axes.
# Separate from the category and axis schema versions.
AMENDMENT_SCHEMA_VERSION = 1

# Parent bill summaries are context only, so they get a much lower cap than bill text
MAX_PARENT_CHARS = 8000

SYSTEM_PROMPT = """
You are an expert political analyst specializing in legislative classification.

Your task is to score one floor amendment to a bill. An amendment isolates one change to
the bill, so you judge the change, not the bill.

Rules:
- Direction: score the direction a YES vote on the amendment moves the bill as it
  currently stands. If the bill is already conservative and the amendment makes it less
  so, a YES vote moves in the liberal direction.
- An amendment that strikes or blocks a provision moves in the opposite direction of that
  provision.
- A substitute amendment is scored on how it differs from the bill, not on its own content.
- Kind: classify the amendment as exactly one of:
    substantive: changes what the bill does in a way that has political content
    technical: corrections, clarifications, conforming changes, or reporting requirements
      with no real political direction
    pro_forma: a symbolic change that does nothing, such as reducing and then increasing
      an account by the same amount ($10) to make a point
    en_bloc: a package of separately numbered amendments offered together en bloc. Use it
      only when the text says the amendment is made up of other numbered amendments. A
      single amendment that touches several topics is substantive.
- A limitation rider ("none of the funds may be used to...") is substantive when the
  activity it blocks has political content. Score it on the policy it blocks.
- Non-substantive kinds may have empty category and axis scores.
- A substantive amendment should score every axis it moves in a clear direction. Leave
  axis_scores empty only when no axis applies at all. For each primary category you
  pick, check the axes whose "Usually relevant to" list names that category.
- Omit any category or axis the amendment does not meaningfully engage.
- Never give an axis a position of 0. If the effect on an axis is neutral or mixed, omit
  that axis instead.
- Apply the boundary rules for each axis exactly as written.
- Judge only the content of the amendment. Never infer a score from its sponsor or party.
- Be objective and consider multiple political perspectives.
"""

USER_INSTRUCTIONS = """Please score the amendment at the end of this message, using the categories and axes defined in the system message.

Provide:
- kind: substantive, technical, pro_forma, or en_bloc
- summary: one sentence on what a YES vote changes about the bill
- primary_categories: the top-level categories the amendment engages. Pick them from the
  amendment itself, not from the parent bill (a rider on a spending bill belongs in the
  rider's own topic). For each:
    - partisan_score: -1.0 to 1.0, where a YES vote moves the bill (-1 = fully toward the
      liberal_view, 1 = fully toward the conservative_view, 0 = neutral or mixed)
    - impact_score: 0.0 to 1.0, how much the amendment matters to that category
    - reasoning: one sentence
- axis_scores: for each relevant axis:
    - position: -1.0 to 1.0, nonzero, the direction a YES vote pushes along the axis
    - relevance: 0.0 to 1.0, how central the axis is to the amendment
    - reasoning: one sentence

Output Format (STRICT JSON)
{
    "kind": "substantive | technical | pro_forma | en_bloc",
    "summary": "string",
    "primary_categories": [{
        "name": "category name exactly as given in the system message",
        "partisan_score": -1.0 to 1.0,
        "impact_score": 0.0 to 1.0,
        "reasoning": "string"
    }],
    "axis_scores": [{
        "axis": "axis id exactly as given in the system message",
        "position": -1.0 to 1.0,
        "relevance": 0.0 to 1.0,
        "reasoning": "string"
    }]
}

CRITICAL: Return ONLY valid JSON. No markdown, no explanation, no text outside the JSON object."""


def format_category(category):
    """Render one primary category definition as prompt text (no subcategories)."""
    return "\n".join(
        [
            f'CATEGORY "{category["name"]}"',
            f'  Scope: {category["scope"]}',
            f'  -1 = liberal_view: {category["liberal_view"]}',
            f'  +1 = conservative_view: {category["conservative_view"]}',
        ]
    )


def primary_category_names():
    return [c["name"] for c in load_categories()["political_categories"]]


@lru_cache(maxsize=None)
def build_system_message(use_reduced=False):
    """Byte-identical for every amendment, so providers can cache it as a shared prefix."""
    categories = load_categories()["political_categories"]
    category_text = "\n\n".join(format_category(c) for c in categories)
    axis_text = "\n\n".join(format_axis(a) for a in llm_axes())
    return f"{SYSTEM_PROMPT}\nCATEGORIES\n{category_text}\n\nAXES\n{axis_text}"


def truncate_parent_text(text):
    """Cut a parent bill summary to MAX_PARENT_CHARS. Returns (text, was_truncated)."""
    if len(text) <= MAX_PARENT_CHARS:
        return text, False
    return text[:MAX_PARENT_CHARS] + "\n\n[Bill summary truncated]", True


def build_retry_note(retry_context):
    """Corrective text appended to the user message after a failed attempt."""
    error_type = retry_context["error_type"]
    bad_names = ", ".join(retry_context["bad_names"])

    if error_type == "bad_axis":
        valid_ids = ", ".join(sorted(a["id"] for a in llm_axes()))
        return f"""

RETRY ATTEMPT: Your previous response used axis ids that do not exist: {bad_names}.
Use only these axis ids, spelled exactly: {valid_ids}."""

    if error_type == "bad_category":
        valid_names = ", ".join(primary_category_names())
        return f"""

RETRY ATTEMPT: Your previous response used category names that do not exist: {bad_names}.
Use only these top-level categories, spelled exactly: {valid_names}."""

    if error_type == "bad_format":
        kinds = ", ".join(AMENDMENT_KINDS)
        return f"""

RETRY ATTEMPT: Your previous response had invalid JSON format and/or did not have the required fields.
"kind" must be one of: {kinds}. Return ONLY valid JSON in exactly the format specified."""

    if error_type == "length":
        return """

RETRY ATTEMPT: Your previous response was cut off before it finished.
Keep every "reasoning" string to one sentence and return complete, valid JSON."""

    return ""


def build_messages(item, use_reduced=False, retry_context=None):
    """
    Chat messages for scoring one amendment. There is no reduced prompt.

    Args:
        item (dict): {"amendment_text", "bill_title", "bill_text", "category_tags",
                      "amended_amendment_purpose" (None unless second-degree)}
        retry_context (dict): {"error_type", "previous_response", "bad_names"} after a failure

    Parent bill context comes before the amendment, so amendments to the same bill share a
    longer cacheable prefix.
    """
    bill_text, _ = truncate_parent_text(item["bill_text"])
    tags = item.get("category_tags") or []
    tags_text = ", ".join(tags) if tags else "(none)"

    parts = [
        USER_INSTRUCTIONS,
        f"PARENT BILL CATEGORY TAGS (context only, do not copy):\n{tags_text}",
        f"PARENT BILL TITLE:\n{item.get('bill_title') or '(unknown)'}",
        f"PARENT BILL SUMMARY:\n{bill_text}",
    ]
    if item.get("amended_amendment_purpose"):
        parts.append(
            "THIS IS A SECOND-DEGREE AMENDMENT. It amends another amendment, which would:\n"
            f"{item['amended_amendment_purpose']}"
        )
    parts.append(f"AMENDMENT:\n{item['amendment_text']}")

    user_message = "\n\n".join(parts)
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
    return amendment_response_format(
        primary_category_names(), [a["id"] for a in llm_axes()]
    )


def normalize_categories(categories, valid_names):
    """
    Clamp scores, fix near-miss names (only without structured outputs), and drop
    duplicates (first one wins). Returns (categories, bad_names).
    """
    result = []
    seen = set()
    bad_names = []

    for category in categories:
        name = category["name"]
        if name not in valid_names and not STRUCTURED_OUTPUTS:
            name = correct_name(name, valid_names)
        if name not in valid_names:
            bad_names.append(category["name"])
            continue
        if name in seen:
            continue
        seen.add(name)

        result.append(
            {
                **category,
                "name": name,
                "partisan_score": max(-1.0, min(1.0, category["partisan_score"])),
                "impact_score": max(0.0, min(1.0, category["impact_score"])),
            }
        )

    return result, bad_names


def parse_response(text):
    """
    Parse and validate a model response.

    Returns:
        (result, error_type, bad_names): error_type is None on success, otherwise
        "bad_format", "bad_category", or "bad_axis".
    """
    try:
        analysis = AmendmentAnalysis.model_validate(
            json.loads(clean_json_response(text.strip()))
        )
    except ValueError:
        # JSONDecodeError and pydantic ValidationError are both ValueErrors
        return None, "bad_format", []

    kind = analysis.kind.strip().lower().replace("-", "_").replace(" ", "_")
    if kind not in AMENDMENT_KINDS:
        return None, "bad_format", []

    categories, bad_categories = normalize_categories(
        [c.model_dump() for c in analysis.primary_categories],
        set(primary_category_names()),
    )
    if bad_categories:
        return None, "bad_category", bad_categories

    axis_scores, bad_axes = normalize_axis_scores(
        [s.model_dump() for s in analysis.axis_scores], {a["id"] for a in llm_axes()}
    )
    if bad_axes:
        return None, "bad_axis", bad_axes
    # A zero position would count as a centrist vote; the prompt says to omit the axis
    axis_scores = [s for s in axis_scores if s["position"] != 0]

    # Same shape as bill_analyses + bill_axis_analyses, so one doc can feed both loaders
    return {
        "kind": kind,
        "summary": analysis.summary,
        "political_categories": {"primary_categories": categories, "subcategories": []},
        "axis_scores": axis_scores,
    }, None, []


def finalize(result, item):
    """Add metadata to a validated analysis."""
    result["amendment_schema_version"] = AMENDMENT_SCHEMA_VERSION
    result["input_source"] = item["input_source"]
    result["bill_truncated"] = len(item["bill_text"]) > MAX_PARENT_CHARS
    return result


def analyze_amendment(item, model, max_retries=4):
    """
    Score one amendment synchronously. Batch mode uses the same functions via batch_runner.

    Args:
        item (dict): see build_messages, plus "input_source"
        model (str): The model to use

    Raises:
        Exception: If the response is still invalid after all retries
    """
    if not item["amendment_text"].strip():
        raise ValueError("Amendment text cannot be empty")

    return analyze_with_retries(
        model,
        lambda use_reduced, retry_context: build_messages(item, use_reduced, retry_context),
        parse_response,
        lambda result: finalize(result, item),
        response_format=get_response_format(),
        max_retries=max_retries,
    )
