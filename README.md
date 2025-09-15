# United States Political Stance Tracker
Analyzes voting records to show where US Congress Members stand on major political issues.

## Setup
Clone this repo and install dependencies in a virtual environment
```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```
Make sure the congress project
 is cloned as a submodule inside congress-data/congress then run this command to download the roll call vote data:
```bash
usc-run votes
```
Follow the [congress repo instructions](https://github.com/unitedstates/congress) to download all necessary packages and tools.

## Usage
Fetches the latest legislator information and saves it locally as YAML:
```bash 
python3 ./get_current_legislators.py
```

Parses all roll call votes for the current Congress and outputs one JSON file per legislator containing their voting record:
```bash
python3 ./process_votes_by_member.py
```
All outputs are stored in the generated ```data/``` folder
