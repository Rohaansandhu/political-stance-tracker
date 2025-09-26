from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
import os
import json

# Load environment variables once when module is imported
load_dotenv()

# Initialize client once when module is imported
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

# Update Schema after every change to prompts
SCHEMA_VERSION = 2

SYSTEM_PROMPT = """
You are an expert political analyst specializing in legislative classification. 

Your task is to analyze bills and determine:

1. POLITICAL CATEGORIES: Classify the bill into relevant policy areas (e.g., economic, social, regulatory, environmental, defense, healthcare, etc.)

2. VOTING POSITION ANALYSIS: For both YES and NO votes, determine:
- Which political position it represents (liberal/progressive vs conservative)
- The underlying political philosophy (more government intervention vs less government intervention)
- Key stakeholder groups that would support/oppose

3. POLITICAL SPECTRUM MAPPING: Place the bill on relevant political spectrums:
- Government role (more regulation vs deregulation)
- Economic policy (progressive vs free market)
- Social policy (progressive vs traditional)

Provide your analysis in a structured format with clear reasoning for each classification.
Be objective and consider multiple political perspectives.
"""

def load_political_frameworks():
    """Load political categories and spectrums from JSON files."""
    with open("political_definitions/political_categories.json", "r") as f:
        categories = json.load(f)
    
    with open("political_definitions/political_spectrums.json", "r") as f:
        spectrums = json.load(f)
    
    return categories, spectrums

# TODO: Determine which free model to use: openai/gpt-oss-120b:free, nvidia/nemotron-nano-9b-v2:free, 
# deepseek/deepseek-chat-v3.1:free, google/gemma-3n-e2b-it:free, x-ai/grok-4-fast:free
def analyze_bill(bill_text, model="openai/gpt-oss-120b:free", max_retries=2):
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
    
    # Load political frameworks
    categories, spectrums = load_political_frameworks()
    
    # Make nested functino for code readability
    def create_user_prompt(is_retry=False, previous_response=None):
        base_prompt = f"""Please analyze the following bill and provide a comprehensive political classification:

                        Political Categories
                        {categories}

                        Political Spectrums
                        {spectrums}

                        BILL TEXT:
                        {bill_text}

                        Please provide:
                        1. Classify the bill into one or more relevant political categories and subcategories. 
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
    
    for attempt in range(max_retries + 1):
        try:
            is_retry = attempt > 0
            user_prompt = create_user_prompt(is_retry, last_response)
            
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
                if attempt > 0:
                    print(f"Successfully parsed JSON on retry attempt {attempt}")
                # Add last_modified field for filtering
                analysis_result["last_modified"] = datetime.now(timezone.utc).isoformat()
                # Add schema_version for future compatibility
                analysis_result["schema_version"] = SCHEMA_VERSION
                return analysis_result
            except json.JSONDecodeError as e:
                if attempt < max_retries:
                    print(f"JSON parse failed on attempt {attempt + 1}, retrying... Error: {e}")
                    continue
                else:
                    raise Exception(f"Failed to parse JSON response after {max_retries + 1} attempts. Last error: {e}\nLast response: {response_content}")
                    
        except Exception as e:
            if "JSON" not in str(e) or attempt >= max_retries:
                raise Exception(f"API call failed: {e}")
            print(f"API call failed on attempt {attempt + 1}, retrying... Error: {e}")
            continue

def _clean_json_response(response_content):
    """Clean up common JSON formatting issues."""
    # Remove markdown code blocks if present
    if response_content.startswith("```"):
        lines = response_content.split('\n')
        # Remove first line if it's ```json or ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_content = '\n'.join(lines)
    
    # Remove any text before the first {
    first_brace = response_content.find('{')
    if first_brace > 0:
        response_content = response_content[first_brace:]
    
    # Remove any text after the last }
    last_brace = response_content.rfind('}')
    if last_brace != -1 and last_brace < len(response_content) - 1:
        response_content = response_content[:last_brace + 1]
    
    return response_content.strip()

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
