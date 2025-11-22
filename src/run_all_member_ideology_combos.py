import subprocess
import argparse
from analysis.bill_analysis_client import SCHEMA_VERSION
import db.db_utils as db_utils

# Models I don't want running, this whole file is really just for me. 
bannedModels = {"openrouter/sherlock-think-alpha","llama-4-scout-17b-16e-instruct", "deepseek/deepseek-chat-v3.1:free", "openai/gpt-oss-20b:free", "x-ai/grok-4-fast:free"}

def get_available_filters(collection):
    """Query MongoDB to find all available values for congress, chamber, and model."""
    collection = db_utils.get_collection(collection)

    congresses = set()
    chambers = set()
    models = set()

    # Use latest schema version by default
    for doc in collection.find({"schema_version": SCHEMA_VERSION}, {"congress": 1, "chamber": 1, "model": 1}):
        if "congress" in doc:
            congresses.add(doc["congress"])
        if "chamber" in doc:
            chambers.add(doc["chamber"])
        if "model" in doc and doc["model"] not in bannedModels:
            models.add(doc["model"])

    # Convert everything to string before sorting to avoid type errors
    congresses = sorted(str(c) for c in congresses)
    chambers = sorted(str(ch) for ch in chambers)
    models = sorted(str(m) for m in models)

    return congresses, chambers, models


def generate_combinations(congresses, chambers):
    """
    Yield all valid combinations of filters, including 'None' for optional args.
    This function looks a little weird due to some commented out code. I've decided
    that the data is only useful to compare Senators to Senators and Reps to Reps.
    """

    # yield {}  # No filters

    # for c in congresses:
    #     yield {"--congress": str(c)}
    for schema_version in ["2", "3"]:
        for ch in chambers:
            yield {"--chamber": ch, "--schema": schema_version}

    # Combine all two-way combinations
    # for c in congresses:
    #     for ch in chambers:
    #         yield {"--congress": str(c), "--chamber": ch}


def run_all_combinations():
    congresses, chambers, models = get_available_filters("bill_analyses")
    print(f"Available congresses: {congresses}")
    print(f"Available chambers: {chambers}")
    print(f"Available models: {models}")

    for model in models:
        for combo in generate_combinations(congresses, chambers):
            cmd = ["python3", "./src/calc_member_ideology.py", "--model", model]
            for k, v in combo.items():
                cmd.append(k)
                cmd.append(v)
            print("Running:", " ".join(cmd))
            subprocess.run(cmd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    run_all_combinations()
