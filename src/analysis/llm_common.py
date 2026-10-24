"""Shared LLM plumbing for the bill category and bill axis analysis clients."""

import os

from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables once when module is imported
load_dotenv()

# Determine which client to use (Specify with env variable)
# Currently supported clients: openrouter or gemini or cerebras or openai
CLIENT = os.getenv("CLIENT")

# Safe for most models (16K tokens)
MAX_BILL_CHARS = 64000

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
elif CLIENT == "openai":
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
    )
else:
    raise ValueError(
        f"Unsupported or missing CLIENT env variable: {CLIENT!r}. "
        "Set CLIENT to one of: openrouter, gemini, cerebras, openai."
    )


def truncate_bill_text(bill_text):
    """Cut bill text down to MAX_BILL_CHARS. Returns (text, was_truncated)."""
    if len(bill_text) <= MAX_BILL_CHARS:
        return bill_text, False

    print(f"Bill truncated: {len(bill_text)} -> {MAX_BILL_CHARS} chars")
    return bill_text[:MAX_BILL_CHARS] + "\n\n[Bill text truncated]", True


def clean_json_response(response_content):
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
