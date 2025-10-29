from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import re
from rapidfuzz import fuzz, process

# Load environment variables once when module is imported
load_dotenv()

# Determine which client to use (Specify with env variable)
# Currently supported clients: openrouter or gemini or cerebras
CLIENT = os.getenv("CLIENT", "openrouter")

# Initialize client once when module is imported
if CLIENT == "openrouter":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
elif CLIENT == "gemini":
    client = OpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY"),
    )
elif CLIENT == "cerebras":
    client = OpenAI(
        base_url="https://api.cerebras.ai/v1",
        api_key=os.environ.get("CEREBRAS_API_KEY"),
    )

# Update Schema after every change to prompts/categories/spectrums
# Schema Version is actually at 3 currently, however, this project is constantly evolving, 
# so I'm using data from both versions at the moment (to get a starting point for the data)
SCHEMA_VERSION = 2

SYSTEM_PROMPT = """
You are an expert political analyst specializing in legislative classification. 

Your task is to analyze bills and determine:

1. POLITICAL CATEGORIES: Classify the bill into relevant policy areas (e.g., economic, social, regulatory, environmental, defense, healthcare, etc.)
- Only use categories and spectrums provided in the user prompt

2. VOTING POSITION ANALYSIS: For both YES and NO votes, determine:
- Which political position it represents (liberal/progressive vs conservative)
- The underlying political philosophy (more government intervention vs less government intervention)
- Key stakeholder groups that would support/oppose

3. POLITICAL SPECTRUM MAPPING: Place the bill on relevant political spectrums.

Provide your analysis in a structured format with clear reasoning for each classification.
Be objective and consider multiple political perspectives.
"""


def load_political_frameworks():
    """Load political categories and spectrums from JSON files."""
    with open("political_definitions/political_categories.json", "r") as f:
        categories = json.load(f)

    with open("political_definitions/political_spectrums.json", "r") as f:
        spectrums = json.load(f)

    with open("political_definitions/reduced_political_categories.json", "r") as f:
        reduced_categories = json.load(f)

    with open("political_definitions/reduced_political_spectrums.json", "r") as f:
        reduced_spectrums = json.load(f)

    return categories, spectrums, reduced_categories, reduced_spectrums


def analyze_bill(bill_text, model="openai/gpt-oss-120b:free", max_retries=3):
    """
    Analyze a political bill and return structured JSON classification.

    Args:
        bill_text (str): The full text of the bill to analyze
        model (str): The model to use for analysis (default: gpt-oss-120b)
        max_retries (int): Maximum number of retry attempts for JSON parsing failures

    Returns:
        dict: Parsed JSON response containing political analysis

    Raises:
        Exception: If API call fails or JSON parsing fails after all retries
    """

    if not bill_text.strip():
        raise ValueError("Bill text cannot be empty")

    # Safe for most models (16K tokens)
    MAX_BILL_CHARS = 64000

    bill_truncated = False
    if len(bill_text) > MAX_BILL_CHARS:
        print(f"Bill truncated: {len(bill_text)} -> {MAX_BILL_CHARS} chars")
        bill_text = bill_text[:MAX_BILL_CHARS] + "\n\n[Bill text truncated]"
        bill_truncated = True

    # Load political frameworks
    categories, spectrums, reduced_categories, reduced_spectrums = (
        load_political_frameworks()
    )

    # Make nested function for code readability
    def create_user_prompt(is_retry=False, previous_response=None, use_reduced=False):
        selected_categories = reduced_categories if use_reduced else categories
        selected_spectrums = reduced_spectrums if use_reduced else spectrums
        base_prompt = f"""Please analyze the following bill and provide a comprehensive political classification:

                        Political Categories
                        {selected_categories}

                        Political Spectrums
                        {selected_spectrums}

                        BILL TEXT:
                        {bill_text}

                        Please provide:
                        1. Classify the bill into one or more relevant political categories and subcategories. 
                           Only use the categories/spectrums provided above. Do NOT create new ones.
                           Determine the impact on each relevant category using a scale from 0.0 to 1.0, where 1.0 is the most impactful.
                           Rate the bill on how conservative/progressive it is within each category. Use a scale of -1 to +1, where:
                            - -1 = fully aligned with the liberal_view
                            - 0 = neutral or mixed
                            - +1 = fully aligned with the conservative_view
                        2. Rate the bill on any relevant political spectrums. Use a scale of -1 to +1, where:
                            - -1 = fully aligned with the left/progressive side
                            - 0 = neutral or mixed
                            - +1 = fully aligned with the right/conservative side
                            Determine the impact on each relevant spectrum using a scale from 0.0 to 1.0, where 1.0 is the most impactful.
                        3. If a spectrum/category is **not relevant**, omit it from the output.
                        4. Analysis of what a YES vote represents politically
                        5. Analysis of what a NO vote represents politically 
                        6. The estimated impact of the bill (how important it is) 

                        Output Format (STRICT JSON)
                        {{
                            "political_categories": {{
                                "primary": {{
                                "name": "string",
                                "partisan_score": -1.0 to 1.0,
                                "impact_score": 0.0 to 1.0,
                                "reasoning": "string"
                                }},
                                "secondary": [{{
                                "name": "string",
                                "partisan_score": -1.0 to 1.0,
                                "impact_score": 0.0 to 1.0,
                                "reasoning": "string"
                                }}],
                                "subcategories": [{{
                                "name": "string",
                                "partisan_score": -1.0 to 1.0,
                                "impact_score": 0.0 to 1.0,
                                "reasoning": "string"
                                }}]
                            }},
                            "political_spectrums": {{
                                "spectrum_name": {{
                                "partisan_score": -1.0 to 1.0,
                                "impact_score": 0.0 to 1.0,
                                "reasoning": "explanation"
                                }}
                            }},
                            "voting_analysis": {{
                                "yes_vote": {{
                                "political_position": "string",
                                "philosophy": "string", 
                                "stakeholder_support": ["array"],
                                "reasoning": "string"
                                }},
                                "no_vote": {{
                                "political_position": "string",
                                "philosophy": "string",
                                "stakeholder_support": ["array"], 
                                "reasoning": "string"
                                }}
                            }},
                            "bill_summary": {{
                                "title": "string",
                                "key_provisions": ["array"],
                                "controversy_level": "low|medium|high",
                                "partisan_divide": "weak|moderate|strong"
                            }}
                        }}

                        CRITICAL: Return ONLY valid JSON. No markdown, no explanation, no text outside the JSON object."""

        if is_retry and previous_response:
            retry_prompt = f"""
                        
                        RETRY ATTEMPT: Your previous response had invalid JSON format:
                        {previous_response[:500]}...
                        
                        Please fix the JSON syntax errors and return ONLY valid JSON."""
            return base_prompt + retry_prompt

        return base_prompt

    last_response = None
    use_reduced = False

    for attempt in range(max_retries + 1):
        try:
            is_retry = attempt > 0
            if use_reduced:
                user_prompt = create_user_prompt(False, None, use_reduced)
            else:
                user_prompt = create_user_prompt(is_retry, last_response, use_reduced)

            # Use temperature of 0 for deterministic output
            completion = client.chat.completions.create(
                extra_body={},
                model=model,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
            )

            # Extract the response content
            response_content = completion.choices[0].message.content.strip()
            last_response = response_content

            # Try to clean up common JSON issues
            response_content = _clean_json_response(response_content)

            # Parse JSON response
            try:
                analysis_result = json.loads(response_content)
                analysis_result = validate(analysis_result, categories, spectrums)
                if attempt > 0:
                    print(f"Successfully parsed JSON on retry attempt {attempt}")
                # Add last_modified field for filtering
                analysis_result["last_modified"] = datetime.now(
                    timezone.utc
                ).isoformat()
                # Add schema_version for future compatibility
                analysis_result["schema_version"] = SCHEMA_VERSION
                # Add if bill was truncated
                analysis_result["bill_truncated"] = bill_truncated
                return analysis_result
            except json.JSONDecodeError as e:
                if attempt < max_retries:
                    print(
                        f"JSON parse failed on attempt {attempt + 1}, retrying... Error: {e}"
                    )
                    continue
                else:
                    raise Exception(
                        f"Failed to parse JSON response after {max_retries + 1} attempts. Last error: {e}\nLast response: {response_content}"
                    )

        except Exception as e:
            # Check if it's a token limit error that needs reduced prompt
            if "context_length" in str(e).lower() or "token" in str(e).lower():
                if not use_reduced:
                    print("Token limit exceeded, retrying with reduced frameworks...")
                    use_reduced = True
                    continue
            # Check if it's a retryable error (JSON or None output)
            if "JSON" in str(e) or "Nonetype" in str(e) and attempt < max_retries:
                print(
                    f"API call failed on attempt {attempt + 1}, retrying... Error: {e}"
                )
                continue

            raise Exception(f"API call failed: {e}")


def _clean_json_response(response_content):
    """Clean up common JSON formatting issues."""
    # Remove markdown code blocks if present
    if response_content.startswith("```"):
        lines = response_content.split("\n")
        # Remove first line if it's ```json or ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_content = "\n".join(lines)

    # Remove any text before the first {
    first_brace = response_content.find("{")
    if first_brace > 0:
        response_content = response_content[first_brace:]

    # Remove any text after the last }
    last_brace = response_content.rfind("}")
    if last_brace != -1 and last_brace < len(response_content) - 1:
        response_content = response_content[: last_brace + 1]

    return response_content.strip()


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


def validate(analysis_result, categories, spectrums):
    """Validate the analysis result against known categories and spectrums."""
    valid_category_names = {cat["name"] for cat in categories["political_categories"]}
    valid_subcategory_names = {
        sub["name"] for cat in categories["political_categories"] for sub in cat.get("subcategories", [])
    }
    valid_spectrum_names = {spec["name"] for spec in spectrums["political_spectrums"]}

    # Validate political categories
    if "political_categories" in analysis_result:
        pcats = analysis_result["political_categories"]
        if "primary" in pcats:
            primary_name = pcats["primary"]["name"]
            pcats["primary"]["name"] = correct_name(primary_name, valid_category_names)
        for sec in pcats.get("secondary", []):
            sec_name = sec["name"]
            sec["name"] = correct_name(sec_name, valid_category_names)

        for sub in pcats.get("subcategories", []):
            sub_name = sub["name"]
            sub["name"] = correct_name(sub_name, valid_subcategory_names)

    # Validate political spectrums
    if "political_spectrums" in analysis_result:
        pspecs = analysis_result["political_spectrums"]
        for spec_name in list(pspecs.keys()):
            fixed_name = correct_name(spec_name, valid_spectrum_names)
            if fixed_name != spec_name:
                pspecs[fixed_name] = pspecs.pop(spec_name)

    return analysis_result


def analyze_bills_batch(bill_texts, model="openai/gpt-oss-120b:free", max_retries=2):
    """
    Analyze multiple bills in batch.

    Args:
        bill_texts (list): List of bill text strings to analyze
        model (str): The model to use for analysis
        max_retries (int): Maximum number of retry attempts per bill

    Returns:
        list: List of analysis results (dicts)
    """
    results = []
    for i, bill_text in enumerate(bill_texts):
        try:
            result = analyze_bill(bill_text, model, max_retries)
            results.append(result)
            print(f"Successfully analyzed bill {i+1}/{len(bill_texts)}")
        except Exception as e:
            print(f"Failed to analyze bill {i+1} after all retry attempts: {e}")
            results.append({"error": str(e)})

    return results
