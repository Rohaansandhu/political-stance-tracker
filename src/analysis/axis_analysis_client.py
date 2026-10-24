import json

from analysis.definitions import llm_axes
from analysis.llm_common import client, clean_json_response, truncate_bill_text
from schema.bill_analyses import AxisAnalysis

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


def create_user_prompt(bill_text, category_tags, axes, retry_note=""):
    axis_text = "\n\n".join(format_axis(a) for a in axes)
    tags_text = ", ".join(category_tags) if category_tags else "(none)"

    prompt = f"""Please score the following bill on the ideological axes below.

AXES
{axis_text}

CATEGORY TAGS ALREADY ASSIGNED TO THIS BILL (context only):
{tags_text}

BILL TEXT:
{bill_text}

For each relevant axis provide:
- position: -1.0 to 1.0, the direction a YES vote pushes along the axis (0 = neutral or mixed)
- relevance: 0.0 to 1.0, how central the axis is to the bill (1.0 = the main point of the bill)
- reasoning: one or two sentences

Output Format (STRICT JSON)
{{
    "axis_scores": [{{
        "axis": "axis id exactly as given above",
        "position": -1.0 to 1.0,
        "relevance": 0.0 to 1.0,
        "reasoning": "string"
    }}]
}}

CRITICAL: Return ONLY valid JSON. No markdown, no explanation, no text outside the JSON object."""

    return prompt + retry_note


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


def analyze_bill_axes(bill_text, category_tags, model, max_retries=4):
    """
    Score a bill on every LLM-scored axis.

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

    bill_text, bill_truncated = truncate_bill_text(bill_text)

    axes = llm_axes()
    valid_ids = {a["id"] for a in axes}

    retry_note = ""
    last_error = None

    for attempt in range(max_retries + 1):
        user_prompt = create_user_prompt(bill_text, category_tags, axes, retry_note)

        completion = client.chat.completions.create(
            extra_body={},
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        response_content = completion.choices[0].message.content or ""

        try:
            result = json.loads(clean_json_response(response_content.strip()))
            analysis = AxisAnalysis.model_validate(result)
        except ValueError as e:
            # JSONDecodeError and pydantic ValidationError are both ValueErrors
            last_error = f"invalid JSON or missing required fields: {e}"
            retry_note = f"""

RETRY ATTEMPT: Your previous response had invalid JSON format and/or did not have the required fields.
Return ONLY valid JSON in exactly the format specified."""
            print(f"Axis analysis attempt {attempt + 1} failed ({last_error[:120]}), retrying...")
            continue

        axis_scores, bad_ids = normalize_axis_scores(
            [s.model_dump() for s in analysis.axis_scores], valid_ids
        )
        if bad_ids:
            last_error = f"unknown axis ids: {', '.join(bad_ids)}"
            retry_note = f"""

RETRY ATTEMPT: Your previous response used axis ids that do not exist: {', '.join(bad_ids)}.
Use only these axis ids, spelled exactly: {', '.join(sorted(valid_ids))}."""
            print(f"Axis analysis attempt {attempt + 1} failed ({last_error}), retrying...")
            continue

        if attempt > 0:
            print(f"Successfully generated axis analysis on retry attempt {attempt}")

        return {
            "axis_scores": axis_scores,
            "axis_schema_version": AXIS_SCHEMA_VERSION,
            "bill_truncated": bill_truncated,
        }

    raise Exception(
        f"Failed to generate a valid axis analysis after {max_retries + 1} attempts. "
        f"Last error: {last_error}"
    )
