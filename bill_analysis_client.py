from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

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

bill_text = ""

# TODO: Determine which free model to use: openai/gpt-oss-120b:free, nvidia/nemotron-nano-9b-v2:free, deepseek/deepseek-chat-v3.1:free, google/gemma-3n-e2b-it:free
# Leaning toward gpt-oss since it is open source and not multi-modal like gemma

completion = client.chat.completions.create(
    extra_body={},
    model="openai/gpt-oss-120b:free",
    messages=[
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": f"""Please analyze the following bill and provide a comprehensive political classification:

                        BILL TEXT:
                        {bill_text}

                        Please provide:
                        1. Primary and secondary political categories
                        2. Analysis of what a YES vote represents politically
                        3. Analysis of what a NO vote represents politically  
                        4. Key political dimensions and positioning
                        5. Likely coalition support/opposition patterns""",
        },
    ],
)
