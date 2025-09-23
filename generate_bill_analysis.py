import json
from pathlib import Path
import bill_analysis_client

# Path to the congress repo data directory
CONGRESS_DATA_DIR = Path("data")

def generate_bill_analyses():
    generated_bills = set()

    # Example path to roll call data: data/{congress}/bills/{bill_type}
    for congress in CONGRESS_DATA_DIR.iterdir():
        if not congress.is_dir():
            continue  

        bills_dir = congress / "bills"
        if not bills_dir.exists():
            print(f"WARNING: No bills folder in {congress}")
            continue

        # Iterate through each bill type folder
        for bill_type_folder in bills_dir.iterdir():
            if not bill_type_folder.is_dir():
                continue

            # Iterate through all individual bill folders
            for folder in bill_type_folder.iterdir():
                if not folder.is_dir():
                    continue

                data_file = folder / "data.json"
                if not data_file.exists():
                    continue

                # Check if the file exists already (skip if so)
                bill_analysis_file = folder / "bill_analysis.json"
                if bill_analysis_file.exists():
                    print(f"{folder.name} has existing analysis")
                    continue

                with open(data_file, "r") as f:
                    bill_data = json.load(f)

                # Get Bill Summary Text
                summary_data = bill_data.get("summary")
                if not summary_data:
                    print(f"ERROR: Couldn't find summary data for {folder.name}")
                    continue
                summart_text = summary_data.get("text")
                if summart_text == "":
                    print(f"ERROR: Couldn't find summary text for {folder.name}")
                    continue

                # Call LLM Client
                bill_analysis = bill_analysis_client.analyze_bill(summart_text, model="x-ai/grok-4-fast:free")

                # Write analysis to folder
                out_file = folder / "bill_analysis.json"
                with open(out_file, "w") as f:
                    json.dump(bill_analysis, f, indent=2)
                
                generated_bills.add(folder.name)
                print(f"{folder.name} bill analysis generated")

    return generated_bills

if __name__ == "__main__":
    bill_count = generate_bill_analyses()
    print(f"Total bill analyses generated: {len(bill_count)}")