import requests
import os
import json


def get_current_legislators():
    """Fetch current legislators JSON and save to data/current_legislators.json"""

    # Load JSON data 
    currentUrl = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

    response = requests.get(currentUrl)
    response.raise_for_status()  # raises an error if request failed
    data = response.json()

    # Make sure output directory exists
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    # File path
    json_path = os.path.join(output_dir, "current_legislators.json")

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"JSON file created at {json_path}")

def get_all_legislators():
    """Fetch historical legislators JSON and save to data/historical_legislators.json"""

    # Load JSON data 
    historicalUrl = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"

    response = requests.get(historicalUrl)
    response.raise_for_status()  # raises an error if request failed
    data = response.json()

    # Make sure output directory exists
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    # File path
    json_path = os.path.join(output_dir, "historical_legislators.json")

    # Write JSON
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"JSON file created at {json_path}")


if __name__ == "__main__":
    get_all_legislators()