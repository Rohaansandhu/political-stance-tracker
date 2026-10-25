import json
from functools import lru_cache

from rapidfuzz import fuzz, process

from analysis.definitions import load_categories, load_reduced_categories
from analysis.llm_common import (
    STRUCTURED_OUTPUTS,
    analyze_with_retries,
    clean_json_response,
    is_truncated,
    truncate_bill_text,
)
from schema.bill_analyses import BillAnalysis, category_response_format

# Update Schema after every change to prompts/categories
SCHEMA_VERSION = 5

SYSTEM_PROMPT = """
You are an expert political analyst specializing in legislative classification. 

Your task is to analyze bills and determine:

1. POLITICAL CATEGORIES: Classify the bill into relevant policy topics (e.g., healthcare, defense, immigration, etc.)
- Only use categories provided below
- A category is a topic. Use its scope to decide whether a bill belongs to it

2. VOTING POSITION ANALYSIS: For both YES and NO votes, determine:
- Which political position it represents (liberal/progressive vs conservative)
- The underlying political philosophy (more government intervention vs less government intervention)
- Key stakeholder groups that would support/oppose

Provide your analysis in a structured format with clear reasoning for each classification.
Be objective and consider multiple political perspectives.
"""

# Static instructions come first in the user message, with the bill last, so everything
# before the bill text is an identical prefix across bills (OpenAI caches long shared prefixes)
USER_INSTRUCTIONS = """Please analyze the following bill and provide a comprehensive political classification.

Please provide:
1. Classify the bill into any relevant political categories and subcategories.
   Each category is a topic described by its scope. Only use the categories provided in the system message. Do NOT create new ones.
   Determine the impact on each relevant category using a scale from 0.0 to 1.0, where 1.0 is the most impactful.
   Rate the bill on how conservative/progressive it is within each category. The liberal_view and conservative_view
   of each category are the two poles of that category's left-right scale. Use a scale of -1 to 1, where:
    - -1 = fully aligned with the liberal_view
    - 0 = neutral or mixed
    - 1 = fully aligned with the conservative_view
3. If a category is **not relevant**, omit it from the output.
4. Analysis of what a YES vote represents politically
5. Analysis of what a NO vote represents politically 

Output Format (STRICT JSON)
{
    "political_categories": {
        "primary_categories": [{
        "name": "string",
        "partisan_score": -1.0 to 1.0,
        "impact_score": 0.0 to 1.0,
        "reasoning": "string"
        }],
        "subcategories": [{
        "name": "string",
        "partisan_score": -1.0 to 1.0,
        "impact_score": 0.0 to 1.0,
        "reasoning": "string"
        }]
    },
    "voting_analysis": {
        "yes_vote": {
        "political_position": "string",
        "philosophy": "string", 
        "stakeholder_support": ["array"],
        "reasoning": "string"
        },
        "no_vote": {
        "political_position": "string",
        "philosophy": "string",
        "stakeholder_support": ["array"], 
        "reasoning": "string"
        }
    },
    "bill_summary": {
        "title": "string",
        "key_provisions": ["array"]
    }
}

CRITICAL: Return ONLY valid JSON. No markdown, no explanation, no text outside the JSON object."""


@lru_cache(maxsize=None)
def build_system_message(use_reduced=False):
    """
    System message with the category definitions. Byte-identical for every bill (cached),
    so providers can cache it as a shared prompt prefix.
    """
    categories = load_reduced_categories() if use_reduced else load_categories()
    definitions = json.dumps(categories, ensure_ascii=False, separators=(",", ":"))
    return f"{SYSTEM_PROMPT}\nPOLITICAL CATEGORIES\n{definitions}"


def build_retry_note(retry_context):
    """Corrective text appended to the user message after a failed attempt."""
    error_type = retry_context["error_type"]
    previous = retry_context["previous_response"][:500]

    # Category outside of defined primary_categories used
    if error_type == "bad_category":
        bad_categories = ", ".join(retry_context["bad_names"])
        return f"""

RETRY ATTEMPT: Your response included {bad_categories} in the primary_categories section, 
but these categories are either subcategories, or misspellling of major categories:
{previous}...

In the primary_categories section, you MUST use only the top-level political categories defined, 
and these categories must be spelled exactly as provided.

Please revise your response by:
Renaming the category name to its exact spelling or by
Removing the bad category name from primary_categories and adding it to subcategories"""

    # JSON format error
    if error_type == "bad_format":
        return f"""

RETRY ATTEMPT: Your previous response had invalid JSON format and/or did not have the required fields:
{previous}...

Please fix the JSON syntax errors and return ONLY valid JSON."""

    # Response was cut off by the output limit
    if error_type == "length":
        return """

RETRY ATTEMPT: Your previous response was cut off before it finished.
Keep every "reasoning" string to one or two sentences and return complete, valid JSON."""

    return ""


def build_messages(bill, use_reduced=False, retry_context=None):
    """
    Chat messages for analyzing one bill.

    Args:
        bill (dict): {"bill_text", "legislative_subjects", "top_subject"}
        use_reduced (bool): Use the reduced category definitions (for context length errors)
        retry_context (dict): {"error_type", "previous_response", "bad_names"} after a failure
    """
    bill_text, _ = truncate_bill_text(bill["bill_text"])
    # Create a comma separated string, joining together all subjects
    subjects_text = ", ".join(bill["legislative_subjects"] or [])

    user_message = f"""{USER_INSTRUCTIONS}

LEGISLATIVE_SUBJECTS:
{subjects_text}

SUBJECTS_TOP_TERM:
{bill["top_subject"]}

BILL TEXT:
{bill_text}"""
    if retry_context:
        user_message += build_retry_note(retry_context)

    return [
        {"role": "system", "content": build_system_message(use_reduced)},
        {"role": "user", "content": user_message},
    ]


@lru_cache(maxsize=None)
def get_response_format():
    """Strict JSON schema for responses, or None when structured outputs aren't in use."""
    if not STRUCTURED_OUTPUTS:
        return None
    categories = load_categories()["political_categories"]
    return category_response_format(
        [c["name"] for c in categories],
        [s["name"] for c in categories for s in c["subcategories"]],
    )


def parse_response(text):
    """
    Parse and validate a model response.

    Returns:
        (result, error_type, bad_names): error_type is None on success, otherwise
        "bad_format" or "bad_category" (with the offending names).
    """
    try:
        result = json.loads(clean_json_response(text.strip()))
    except json.JSONDecodeError:
        return None, "bad_format", []

    # Check if result json has all required fields
    if not validate(result):
        return None, "bad_format", []

    # Check if category names match the categories supplied
    result, _, bad_categories = validate_names(result, load_categories())
    if bad_categories:
        return None, "bad_category", bad_categories

    return result, None, []


def finalize(result, bill_truncated):
    """Add metadata to a validated analysis."""
    # Add schema_version for future compatibility
    result["schema_version"] = SCHEMA_VERSION
    # Add if bill was truncated
    result["bill_truncated"] = bill_truncated
    return result


def analyze_bill(bill_text, legislative_subjects, top_subject, model, max_retries=4):
    """
    Analyze a political bill and return its structured classification (synchronously).
    Batch mode (analysis/batch_runner.py) uses the same build_messages/parse_response/finalize.

    Args:
        bill_text (str): The full text of the bill to analyze
        model (str): The model to use for analysis
        max_retries (int): Maximum number of retry attempts

    Returns:
        dict: Validated analysis

    Raises:
        Exception: If the API call fails or no valid response is produced after all retries
    """
    if not bill_text.strip():
        raise ValueError("Bill text cannot be empty")

    bill = {
        "bill_text": bill_text,
        "legislative_subjects": legislative_subjects,
        "top_subject": top_subject,
    }
    bill_truncated = is_truncated(bill_text)

    return analyze_with_retries(
        model,
        lambda use_reduced, retry_context: build_messages(bill, use_reduced, retry_context),
        parse_response,
        lambda result: finalize(result, bill_truncated),
        response_format=get_response_format(),
        max_retries=max_retries,
    )


def correct_name(name, valid_names, score_threshold=70):
    """
    Return the corrected name if a close match exists in valid_names.
    Uses RapidFuzz for faster and more accurate matching (builds off of difflib and fuzzywuzzy)
    Score threshold default is at 70, any lower may cause innacurate matches.
    """
    if not name or not valid_names:
        return name

    # exists in name or is substring of a valid name or vice versa
    for valid_name in valid_names:
        if name in valid_name or valid_name in name:
            return valid_name

    # Find the best match and its similarity score
    match = process.extractOne(name, valid_names, scorer=fuzz.token_sort_ratio)
    if match and match[1] >= score_threshold:
        corrected_name = match[0]
        if corrected_name != name:
            print(
                f"[correct_name] Corrected '{name}' → '{corrected_name}' (score: {match[1]:.1f})"
            )
        return match[0]
    else:
        if match:
            print(
                f"[correct_name] No suitable match for '{name}' (best: '{match[0]}' @ {match[1]:.1f} < {score_threshold})"
            )
        else:
            print(f"[correct_name] No matches found for '{name}'")
    return name


def validate(analysis_result):
    try:
        BillAnalysis.model_validate(analysis_result)
        return True
    except Exception:
        return False


def validate_names(analysis_result, categories):
    """Validate the analysis result against known categories."""
    valid_category_names = {cat["name"] for cat in categories["political_categories"]}

    # Set to True if we couldn't fix names manually
    reprompt = False

    bad_categories = []

    # Correct names of political categories
    if "political_categories" in analysis_result:
        cats = analysis_result["political_categories"]
        if "primary_categories" in cats:
            pcats = analysis_result["political_categories"]["primary_categories"]
            for prim in pcats:
                prim_name = prim["name"]
                prim["name"] = correct_name(prim_name, valid_category_names)
                # At least one primary category could not be fixed
                if prim["name"] not in valid_category_names:
                    bad_categories.append(prim["name"])
                    reprompt = True

    return analysis_result, reprompt, bad_categories
