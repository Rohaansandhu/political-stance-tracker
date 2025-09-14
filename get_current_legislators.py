import requests
import os
import yaml

# Load YAML data 
url = "https://unitedstates.github.io/congress-legislators/legislators-current.yaml"
response = requests.get(url)
data = yaml.safe_load(response.text)

# Make sure output directory exists
output_dir = "data"
os.makedirs(output_dir, exist_ok=True)

# File path
yaml_path = os.path.join(output_dir, "legislator.yaml")

# Write YAML
with open(yaml_path, "w") as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)

print(f"YAML file created at {yaml_path}")