import requests
import os
import json

# Load JSON data 
url = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
response = requests.get(url)
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