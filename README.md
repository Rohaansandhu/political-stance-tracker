# United States Political Stance Tracker
Analyzes voting records to show where US Congress Members stand on major political issues and ideological axes. Built upon the amazing tools of [unitedstates/congress](https://github.com/unitedstates/congress).

If you're curious about the role of LLMs in legislation summaries and possible political analysis, I'd highly recommend checking out this paper from 2024 [political-llm.org](https://political-llm.org/). Another great research piece was produced last May, discussing an LLM-driven framework for scaling Japanese parliamentary members using their political speeches [KOKKAI DOC](https://arxiv.org/pdf/2505.07118).

## Website Is Up! [Political Stance Tracker Website](https://www.uspolitrack.org)
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
python3 src/get_votes.py [--chamber=house|senate] [--congress=NUM] [--session=NUM] [--force]
```
- `--chamber`: `house` (default) or `senate`.
- `--congress`, `--session`: Specify which congress/session to fetch (defaults to the current congress, every started session).
- `--force`: Force re-download of all vote data.

**House:** `clerk.house.gov` no longer serves the vote index pages that `usc-run votes` depends on, so new House votes are found by trying roll numbers in order (including any gaps) until the clerk has no more. Votes are saved to `data/{congress}/votes/{session}/`.

Make sure you run these commands in the top-level directory so the data populates in the correct folder.


### 1. Process Votes by Member
Organizes roll call votes by member, from MongoDB, and outputs to MongoDB (or data/).
```bash
python3 src/process_votes_by_member.py [--writeData]
```
Options:
- `--writeData`: If specified will also store to the data/ folder

Output: MongoDB 'members_with_votes' and 'member_votes' collections.

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
- `--bill_ids`: Only process these bill ids (e.g. a pilot set from `python3 src/select_pilot_bills.py`).
- `--mode sync|batch`: `sync` (default) works with any provider. `batch` uses the OpenAI Batch API (see below).

Output: MongoDB bill_analyses collection

#### Batch mode (OpenAI only, about half the price, results within 24 hours)
Needs `CLIENT=openai`. Works the same for step 3 and 3b (`generate_bill_analysis.py` and `generate_axis_analysis.py`), and every job is recorded in the `llm_batches` collection, so you can stop and resume. Submit the axis pass only after the category pass has been collected, since axis prompts use the bill's category tags.
```bash
python3 src/generate_bill_analysis.py --mode batch submit [--force] [--bill_ids ...] [--numOfBills N] [--dry_run]
python3 src/generate_bill_analysis.py --mode batch status
python3 src/generate_bill_analysis.py --mode batch collect
python3 src/generate_bill_analysis.py --mode batch retry
```
- `submit`: one request per bill, chunked (`--chunk_size`, default 500 bills per batch). Never resubmits bills that already have an analysis, are in an open batch, or are awaiting retry. `--dry_run` prints the estimated tokens and cost without submitting. A submission over `--max_cost` (default $5), or with an unknown cost, needs `--confirm`.
- `collect`: downloads finished batches, validates, saves analyses, and records token usage per bill (printed with the real cost).
- `retry`: resubmits failures only, with the corrective retry prompts. After 2 retry rounds, the remainder goes through the sync path.

Responses use OpenAI Structured Outputs (strict JSON schema, with category and axis names as enums), which removes most bad-JSON and misspelled-category retries. Also set a spending limit in the OpenAI dashboard.

Optional settings (environment variables):
- `LLM_PRICE_INPUT_PER_M`, `LLM_PRICE_OUTPUT_PER_M`, `LLM_PRICE_CACHED_INPUT_PER_M`: your model's normal $/1M-token prices (batch discount is applied automatically). Without them, cost estimates are unknown and `submit` requires `--confirm`.
- `LLM_MAX_COMPLETION_TOKENS` (default 16000): per-request output cap, including reasoning tokens.
- `LLM_REASONING_EFFORT`: sent as `reasoning_effort` when set (the model must support it).
- `LLM_EXPECTED_REASONING_TOKENS`: per-bill reasoning tokens to assume in estimates (default 0).
- `LLM_STRUCTURED_OUTPUTS=0`: turn off Structured Outputs for a model that doesn't support them.
- Install `tiktoken` for accurate token counts (otherwise ~4 characters per token is assumed).

### 3b. Generate Bill Axis Analyses
Categories are issue topics; **axes** are viewpoints that cut across them (see `political_definitions/political_axes.json`). This is a separate LLM pass over bills that already have a category analysis from step 3 (same `MODEL`), so category analyses never need regenerating when axes change.
```bash
python3 src/generate_axis_analysis.py [--force] [--numOfBills num] [--delay seconds]
```
Output: MongoDB bill_axis_analyses collection (keyed by bill_id + model + axis schema version). The `institutional` axis is not scored here; it is computed from roll calls in step 4. Run `python3 src/db/db_utils.py` once to create the collection's unique index.

### 4. Calculate Member Ideology
Processes all legislators and calculates ideology scores based on their voting records and bill analyses. Please specify model (required) and schema version of the bill analyses you want to use. Profiles contain category scores plus an `axes` entry per axis (score, bill count, per-category breakdown, within-party z-score). If no axis analyses exist yet, profiles only get category scores.
```bash
python3 src/calc_member_ideology.py [--model model] [--schema schema] [--axis_schema axis_schema] [--congress congress] [--chamber chamber] [--bill_type type1 type2...]
```
Options:
- `--model`: Specify the model to analyze data from
- `--schema`: Specify the schema version to analyze data from (optional, will default to latest)
- `--axis_schema`: Specify the axis schema version to analyze data from (optional, will default to latest)
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

### 7. Validate Axes
Checks that the axes are worth building on: axis correlations (across all members and within each party, since party sorting inflates the all-member figures), PCA on the member x axis matrix, known cross-party votes (`political_definitions/validation_bills.json`), inter-model agreement, and observed axis x category applicability. Results print and are saved to `data/validation/<spec_hash>/`.
```bash
python3 src/validate_axes.py --spec_hash spec_hash [--compare_models modelA modelB]
```

### Institutional axis
`python3 src/calc_institutional.py [--congress num] [--chamber house|senate]` prints the most and least party-loyal members. It is a sanity check; step 4 runs the same calculation.

### Publishing the definitions
`python3 src/load_definitions.py [--target local|cloud|both] [--dry_run]` replaces the single document in the `categories` collection with `political_categories.json` and the single document in the `axes` collection with `political_axes.json`. The old document is saved to `data/definition_backups/` first. Rerun it whenever a definition file changes.

### Syncing to the cloud DB
`src/cloud_db_updater.py` syncs one collection per call and is generic. It syncs every local document unless you pass `--query` with a JSON MongoDB filter, and `--dry_run` reports what would be synced without writing. For example, to sync the schema v5 axis analyses:
```bash
python3 src/cloud_db_updater.py --local_collection bill_axis_analyses --cloud_collection bill_axis_analyses --key_fields bill_id,model,axis_schema_version --query '{"axis_schema_version": 1}' --dry_run
```
Remove `--dry_run` to write. 

## Data Output
All outputs are stored in MongoDB or the generated `data/` folder.

If you have suggestions or want to contribute, feel free to open an issue or pull request!

## Acknowledgments
Special thanks to the [unitedstates/congress](https://github.com/unitedstates/congress) project for providing the House and Senate scrapers that make this project possible. 
