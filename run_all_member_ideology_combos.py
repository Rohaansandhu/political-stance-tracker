import subprocess
import argparse
import db_utils

INPUT_COLLECTION = "bill_analyses"
# Models I don't want running
bannedModels = ["llama-4-scout-17b-16e-instruct", "deepseek/deepseek-chat-v3.1:free", "gemini-2.5-flash-lite", "openai/gpt-oss-20b:free"]

def get_available_filters():
    """Query MongoDB to find all available values for congress, chamber, and model."""
    collection = db_utils.get_collection(INPUT_COLLECTION)

    congresses = set()
    chambers = set()
    models = set()

    for doc in collection.find({}, {"congress": 1, "chamber": 1, "model": 1}):
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
    """Yield all valid combinations of filters, including 'None' for optional args."""
    yield {}  # No filters

    for c in congresses:
        yield {"--congress": str(c)}
    for ch in chambers:
        yield {"--chamber": ch}

    # Combine all two-way combinations
    for c in congresses:
        for ch in chambers:
            yield {"--congress": str(c), "--chamber": ch}


def run_all_combinations():
    congresses, chambers, models = get_available_filters()
    print(f"Available congresses: {congresses}")
    print(f"Available chambers: {chambers}")
    print(f"Available models: {models}")

    for model in models:
        for combo in generate_combinations(congresses, chambers):
            cmd = ["python3", "./calc_member_ideology.py", "--model", model]
            for k, v in combo.items():
                cmd.append(k)
                cmd.append(v)
            print("Running:", " ".join(cmd))
            subprocess.run(cmd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    run_all_combinations()
