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

# Structured Outputs (strict JSON schema responses) are only used with OpenAI, since other
# providers' OpenAI-compatible endpoints don't reliably support them. Set
# LLM_STRUCTURED_OUTPUTS=0 to turn them off for a model that doesn't support them.
STRUCTURED_OUTPUTS = CLIENT == "openai" and os.getenv("LLM_STRUCTURED_OUTPUTS", "1") != "0"

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


def is_truncated(bill_text):
    """Whether truncate_bill_text would cut this text."""
    return len(bill_text) > MAX_BILL_CHARS


def truncate_bill_text(bill_text):
    """Cut bill text down to MAX_BILL_CHARS. Returns (text, was_truncated)."""
    if not is_truncated(bill_text):
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


def is_context_length_error(error):
    """Whether an API error means the prompt was too long for the model."""
    message = str(error).lower()
    return "context_length" in message or "context length" in message


def complete(model, messages, response_format=None):
    """One chat completion. Returns the response text (empty if the model returned none)."""
    kwargs = {"response_format": response_format} if response_format else {}
    completion = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return (completion.choices[0].message.content or "").strip()


def analyze_with_retries(
    model,
    build_messages,
    parse_response,
    finalize,
    response_format=None,
    max_retries=4,
):
    """
    Run one analysis synchronously: call the model, parse the response, and retry with a
    corrective prompt on failure.

    Args:
        build_messages: (use_reduced, retry_context) -> messages
        parse_response: text -> (result, error_type, bad_names); error_type None means success
        finalize: result -> final result dict
        response_format: optional strict JSON schema response_format

    A retry_context is {"error_type", "previous_response", "bad_names"}. A context length
    error switches to the reduced prompt (use_reduced=True) once.
    """
    use_reduced = False
    retry_context = None
    last_error = None

    for attempt in range(max_retries + 1):
        messages = build_messages(use_reduced, retry_context)
        try:
            text = complete(model, messages, response_format)
        except Exception as e:
            if is_context_length_error(e) and not use_reduced:
                print("Context length exceeded, retrying with reduced prompt...")
                use_reduced = True
                continue
            raise Exception(f"API call failed: {e}")

        result, error_type, bad_names = parse_response(text)
        if error_type is None:
            if attempt > 0:
                print(f"Successfully generated analysis on retry attempt {attempt}")
            return finalize(result)

        last_error = f"{error_type} {', '.join(bad_names)}".strip()
        print(f"Attempt {attempt + 1} failed ({last_error}), retrying...")
        retry_context = {
            "error_type": error_type,
            "previous_response": text,
            "bad_names": bad_names,
        }

    raise Exception(
        f"Failed to generate a valid analysis after {max_retries + 1} attempts. "
        f"Last error: {last_error}"
    )
