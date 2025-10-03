
# United States Political Stance Tracker
Analyzes voting records to show where US Congress Members stand on major political issues and spectrums. Built upon the amazing tools of [unitedstates/congress](https://github.com/unitedstates/congress).


## Setup
Clone this repo and install dependencies in a virtual environment:
```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### Congres Project Required!!!
Make sure the congress project is cloned as a submodule inside this repo.
Follow the [congress repo instructions](https://github.com/unitedstates/congress) to download all necessary packages and tools.

### MongoDB Setup
This project uses MongoDB to store and query vote and bill data. This is optional! With the correct flags on each script, you can continue to store data in the data/ directory only. You can start and stop the database using the provided scripts:

**Start MongoDB:**
```bash
python3 start_mongod.py
```
This will start a local MongoDB server on port 27017 by default, with data stored in `data/db` and logs in `data/mongodlogs/`. Replica set support is available (see script for details).

**Stop MongoDB:**
```bash
python3 stop_mongod.py
```
This will cleanly shut down the MongoDB server.

You must have MongoDB installed and available in your system PATH. For more advanced configuration, feel free to edit `start_mongod.py`.

### LLM API Key Required
To use the bill analysis features, you must create your own [OpenRouter](https://openrouter.ai/) API key and set it as an environment variable. This allows you to use the LLM of your choice for bill analysis.

1. Sign up at [OpenRouter](https://openrouter.ai/) and obtain your API key.
2. Set your API key in your .env file or run this (replace `YOUR_API_KEY`):
  ```bash
  export OPENROUTER_API_KEY=YOUR_API_KEY
  ```
3. You can now use the LLM-powered scripts in this project.

## Usage

### 0. Fetch Roll Call Data
Run the following script to fetch roll call vote data and automatically load it into the database:

```bash
python3 get_votes.py [--no_db] [--congress=NUM] [--session=NUM] [--sessions=LIST] [--force] [--fast]
```
- `--no_db`: Only process and save to the `data/` folder, do not load into the database.
- `--congress`, `--session`, `--sessions`: Specify which congress/session(s) to fetch.
- `--force`: Force re-download of all vote data.
- `--fast`: Only pull data from the last 3 days.

This script internally calls the `usc-run votes` command, which downloads all voting roll call data into the `data/{congress}/votes` directory.  
You can also run `usc-run votes` directly with its options if you prefer:
```bash
usc-run votes [--congress=NUM] [--session=NUM] [--sessions=LIST] [--force] [--fast]
```
Make sure you run these commands in the top-level directory so the data populates in the correct folder.


### 1. Process Votes by Member
Organizes roll call votes by member, from either the data/ directory or MongoDB, and outputs to data/organized_votes or MongoDB.
```bash
python3 process_votes_by_member.py [--input] [--output]
```
Options:
- `--input`: Source of roll call votes (`mongodb`, `data`, or `both`). Ex: --input mongodb
- `--output`: Store to (`mongodb`, `data`, or `both`). Ex: --output data

Output: `data/organized_votes/{member_id}.json` and/or MongoDB 'member_votes' collection.

### 2. Get Voted Bills
Fetches bill status for all bills that have been voted on and marks them accordingly. Also generates `data.json` files for bill XML data.
```bash
python3 get_voted_bills.py [--force]
```
Use `--force` to re-download and re-parse all bill data.

### 3. Generate Bill Analyses
Analyzes bills using an LLM and generates a `bill_analysis.json` for each voted bill. Can process bills from local files, MongoDB, or both.
```bash
python3 generate_bill_analysis.py [--force] [--numOfBills N] [--no_db] [--only_db]
```
Options:
- `--force`: Overwrite existing analyses and update outdated schemas.
- `--numOfBills N`: Only process N bills (useful for testing or limiting API usage).
- `--no_db`: Only process and save to data/ folder, do not load to/from the database.
- `--only_db`: Only process bills from the database, skip local files.

### 4. Calculate Member Ideology
Processes all legislators and calculates ideology scores based on their voting records and bill analyses.
```bash
python3 calc_member_ideology.py
```
Output: `data/legislator_profiles/{member_id}.json`


### 5. Member Ranking
Ranks legislators by political spectrums and categories, generates summary reports and CSV exports.
```bash
python3 member_ranking.py
```
Outputs:
- `data/rankings/all_rankings.json` (full rankings)
- `data/rankings/extremes_summary.json` (summary of most extreme legislators)
- `data/rankings/csv/` (CSV exports)

### 6. Visualize Rankings
Generates plots and visualizations from the CSV ranking outputs. Plots are saved in `data/rankings/csv/plots/`.
```bash
python3 visualize_rankings.py
```
Outputs:
- Boxplots of ideological score distributions by party for each ranking
- Scatter plots of rank vs. score, colored by party
- All plots saved as PNG files in `data/rankings/csv/plots/`

### 7. Load Data to Database
Loads all data from the data/ directory into MongoDB collections. You can comment/uncomment functions in the script to control which collections are loaded.
```bash
python3 load_to_db.py
```
By default, loads bill data and analyses. You can also load votes, member-organized votes, and legislator profiles by uncommenting the relevant function calls in `main()`.
Collections used:
- `bill_data`, `bill_analyses`, `rollcall_votes`, `member_votes`, `legislator_profiles`

## Data Output
All outputs are stored in the generated `data/` folder and its subdirectories.

## Current State & TODOs
This project is actively under development. The following features and improvements are planned or in progress:

- **Store ideology outputs in a Relational DB:** Enhance query and graphing capabilities by creating rigid schemas, allowing for more complex data analysis. <br>
  *Progress: Not started.*
- **Use MongoDB to store raw data:** Improve data storage by storing the raw json outputs from both the Congress scrapers and the LLM into MongoDB. <br>
  *Progress: Script to load existing data from data/ into the db has been created. Will add options for current scripts to output directly into db.*
- **Refine bill analysis LLM prompts:** Improve the quality and consistency of bill analyses generated by the LLM. LLM responses vary by nature, but by creating a proper frame and structure, and by adjusting model temperatures, a more predictable and accurate response can be achieved. <br>
  *Progress:Added versioning to model prompts, put temperature at 0. Refined the categories and spectrums, gave partisan and impact scores to both.*
- **Resolve data irregularities:** Address issues with current rankings and political stances to ensure accuracy and reliability. Spectrum ranges are extremely variable. Libertarian and Authoritarian leanings need to be implemented better. <br>
  *Progress: Bill votes for small amendment changes happen often. Now only the bill votes on passage are included. Political categories and spectrums still need more vetting and additions.*
- **Add graphs and visualizations:** Integrate graphical outputs to better illustrate findings and trends. <br>
  *Progress: Added two scripts to generate csvs and graphs (boxplots and scatterplots).*
- **Add results folder for findings:** Create a dedicated folder to store summary results, visualizations, and key insights generated to show this tool's output. <br>
  *Progress: Will add soon for initial findings. Once more data is crunched and organized, the results will be made public. The plan is to gather data from the 113th to 119th congress.*

If you have suggestions or want to contribute, feel free to open an issue or pull request!

## Acknowledgments
Special thanks to the [unitedstates/congress](https://github.com/unitedstates/congress) project for providing the House and Senate scrapers that make this project possible.
