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

def analyze_bill(bill_text, model="openai/gpt-oss-120b:free"):
    """
    Analyze a political bill and return structured JSON classification.
    
    Args:
        bill_text (str): The full text of the bill to analyze
        model (str): The model to use for analysis (default: gpt-oss-120b)
    
    Returns:
        dict: Parsed JSON response containing political analysis
    
    Raises:
        Exception: If API call fails or JSON parsing fails
    """
    if not bill_text.strip():
        raise ValueError("Bill text cannot be empty")
    
    # Load political frameworks
    categories, spectrums = load_political_frameworks()
    
    try:
        completion = client.chat.completions.create(
            extra_body={},
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"""Please analyze the following bill and provide a comprehensive political classification:

                                Political Categories
                                {categories}

                                Political Spectrums
                                {spectrums}

                                BILL TEXT:
                                {bill_text}

                                Please provide:
                                1. Classify the bill into one or more relevant political categories and subcategories.
                                2. Rate the bill on any relevant political spectrums. Use a scale of -1 to +1, where:
                                    - -1 = fully aligned with the left/progressive side
                                    - 0 = neutral or mixed
                                    - +1 = fully aligned with the right/conservative side
                                3. If a spectrum is **not relevant**, omit it from the output.
                                4. Analysis of what a YES vote represents politically
                                5. Analysis of what a NO vote represents politically  

                                Output Format (STRICT JSON)
                                {{
                                    "political_categories": {{
                                        "primary": "string",
                                        "secondary": ["array", "of", "strings"],
                                        "subcategories": ["array", "of", "specific", "subcategories"]
                                    }},
                                    "political_spectrums": {{
                                        "spectrum_name": {{
                                        "score": -1.0 to 1.0,
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

                                Only return JSON. Do not include natural language outside the JSON object.
                                """,
                },
            ],
        )
        
        # Extract the response content
        response_content = completion.choices[0].message.content
        
        # Parse JSON response
        try:
            analysis_result = json.loads(response_content)
            return analysis_result
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse JSON response: {e}\nRaw response: {response_content}")
            
    except Exception as e:
        raise Exception(f"API call failed: {e}")

def analyze_bills_batch(bill_texts, model="openai/gpt-oss-120b:free"):
    """
    Analyze multiple bills in batch.
    
    Args:
        bill_texts (list): List of bill text strings to analyze
        model (str): The model to use for analysis
    
    Returns:
        list: List of analysis results (dicts)
    """
    results = []
    for i, bill_text in enumerate(bill_texts):
        try:
            result = analyze_bill(bill_text, model)
            results.append(result)
            print(f"Successfully analyzed bill {i+1}/{len(bill_texts)}")
        except Exception as e:
            print(f"Failed to analyze bill {i+1}: {e}")
            results.append({"error": str(e)})
    
    return results
