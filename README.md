# United States Political Stance Tracker
Analyzes voting records to show where US Congress Members stand on major political issues and spectrums. Built upon the amazing tools of [unitedstates/congress](https://github.com/unitedstates/congress).

If you're curious about the role of LLMs in legislation summaries and possible political analysis, I'd highly recommend checking out this paper from 2024 [political-llm.org](https://political-llm.org/). Another great research piece was produced last May, discussing an LLM-driven framework for scaling Japanese parliamentary members using their political speeches [KOKKAI DOC](https://arxiv.org/pdf/2505.07118).

## Website Is Up! [Political Stance Tracker Website](https://political-stance-website-frontend.vercel.app/)
A website with my initial findings and results are up. However there's still:
- More UI improvements to come.
- New features on the way.
- More data ready to be analyzed.

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

### MongoDB Setup (Required!)
This project uses MongoDB to store and query vote and bill data. However, you'll notice a lot of initial scripts (that pull data from congress) will still store to the data/ directory as well. You can start and stop the database using the provided scripts:

**Start MongoDB:**
```bash
python3 src/db/start_mongod.py
```
This will start a local MongoDB server on port 27017 by default, with data stored in `data/db` and logs in `data/mongodlogs/`. Replica set support is available (see script for details).

**Stop MongoDB:**
```bash
python3 src/db/stop_mongod.py
```
This will cleanly shut down the MongoDB server.

You must have MongoDB installed and available in your system PATH. For more advanced configuration, feel free to edit `start_mongod.py`.

### LLM API Key Required
To use the bill analysis features, you must create your own LLM API Key using one of the supported providers:
[OpenRouter](https://openrouter.ai/), [Google Ai Studio](https://ai.google.dev/gemini-api/docs), [Cerebras](https://www.cerebras.ai/).
This allows you to use the LLM of your choice for bill analysis.

1. Set your API key in your .env file (recommended) or run this (replace `YOUR_API_KEY` with the correct api key from your provider of choice):
  ```bash
  export OPENROUTER_API_KEY=YOUR_API_KEY
  export GEMINI_API_KEY=YOUR_API_KEY
  export CEREBRAS_API_KEY=YOUR_API_KEY
  ```
2. Also, depending on which provider you choose, you will need to specify the client: (`openrouter`, `gemini`, or `cerebras`)
```bash
  export CLIENT=YOUR_CLIENT_CHOICE
  ```
3. You can now use the LLM-powered scripts in this project.
4. (Optional) All providers are accessed through the OpenAI Client Libraries. If you wish, you can find another provider with this library and add it to bill_analysis_client.py with ease.


### Environment Variables To Set
Here is a list of all environment variables needed in this project:
- `OPENROUTER_API_KEY` if you use openrouter
- `GEMINI_API_KEY` if you use gemini
- `CEREBRAS_API_KEY` if you use cerebras
- `CLIENT` to specify between openrouter, gemini, and cerebras
- `MODEL` to specify the model name from the client
- `MONGO_URI` to specify the db instance
- `DB_NAME` to specify the db name
- `CLOUD_URI` (optional) to specify the cloud db instance

## Usage

### 0. Fetch Roll Call Data
Run the following script to fetch roll call vote data and automatically load it into the database:

```bash
python3 src/get_votes.py [--congress=NUM] [--session=NUM] [--sessions=LIST] [--force] [--fast]
```
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
Organizes roll call votes by member, from MongoDB, and outputs to MongoDB (or data/).
```bash
python3 src/process_votes_by_member.py [--writeData]
```
Options:
- `--writeData`: If specified will also store to the data/ folder

Output: MongoDB 'member_votes' collection.

### 2. Get Voted Bills
Fetches bill status for all bills that have been voted on and marks them accordingly. Also generates `data.json` files for bill XML data. Automatically uploads bill data to MongoDB.
```bash
python3 src/get_voted_bills.py [--force]
```
Use `--force` to re-download and re-parse all bill data.

Output: bill_data collection

### 3. Generate Bill Analyses
Analyzes bills using an LLM and generates a `bill_analysis.json` for each voted bill. Processes bills from MongoDB.
```bash
python3 src/generate_bill_analysis.py [--force] [--numOfBills num] 
```
Options:
- `--force`: Overwrite existing analyses and update outdated schemas.
- `--numOfBills`: Only process num bills (useful for testing or limiting API usage).

Output: MongoDB bill_analyses collection

### 4. Calculate Member Ideology
Processes all legislators and calculates ideology scores based on their voting records and bill analyses. Please specify model (required) and schema version of the bill analyses you want to use.
```bash
python3 src/calc_member_ideology.py [--model model] [--schema schema] [--congress congress] [--chamber chamber] [--bill_type type1 type2...]
```
Options:
- `--model`: Specify the model to analyze data from
- `--schema`: Specify the schema version to analyze data from (optional, will default to latest)
- `--congress`: (Optional) Specify the congress
- `--chamber`: (Optional) Specify the chamber (house, senate)
- `--bill_type`: (Optional) Specify one or more bill types

Output: legislator_profiles collection

### 5. Create Plots
Uses the legislator_profiles collection to create histograms and boxplots of congress members' data. You must specify the spec_hash of the profiles you want to create plots from. 
```bash
python3 src/create_plots.py [--spec_hash spec_hash]
```
Options:
- `--spec_hash`: (REQUIRED) Specify the specific legislator profiles to make plots with


### 6. Create Rankings
Uses the legislator_profiles collection to create histograms and boxoplots of congress members' data. You must specify the spec_hash of the profiles you want to create plots from.
```bash
python3 src/create_rankings.py [--spec_hash spec_hash]
```
Options:
- `--spec_hash`: (REQUIRED) Specify the specific legislator profiles to make plots with

## Data Output
All outputs are stored in MongoDB or the generated `data/` folder.

## Current State
This project is actively under development. Here's a quick summary of recent improvements:
- Improved category validation for bill analyses with prompting
- Added a Cloud DB updater script
- Excluded partisan scores of 0
  - If included, leads to longer serving senators/reps appearing more moderate, just due to bipartisan bills that go through
- Rigid Schema for Bill Analyses, added validation of JSON fields
- Added initial script for finding key_stakeholders of each legislator
- Split up aggregated stats into 2 collections
- Added bulk_write operations
- Schema Update to v3
  - Added legislative subjects from bill data for consideration
  - Deleted political spectrums
    - Moved spectrums in categories, LLMs would get confused sometimes and place categories in spectrums and vice versa
  - Refined political categories
    - Only primary categories and subcategories now, secondary categories just confused the LLM
  - Slight text prompt adjustments

If you have suggestions or want to contribute, feel free to open an issue or pull request!

## Acknowledgments
Special thanks to the [unitedstates/congress](https://github.com/unitedstates/congress) project for providing the House and Senate scrapers that make this project possible. 
