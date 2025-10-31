import argparse
import re
import numpy as np
import pandas as pd
from pathlib import Path
import db_utils

def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters with underscores."""
    return re.sub(r"[^\w\-_.]", "_", name)


def get_output_dir(spec_hash):
    """Construct and ensure output directory exists for rankings."""
    sanitezed_hash = sanitize_filename(spec_hash)
    output_dir = Path("data/rankings") / sanitezed_hash
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_profiles(spec_hash):
    """Load legislator_profiles and return DataFrames similar to create_plots.py."""
    query = {"spec_hash": spec_hash}
    profile_coll = db_utils.get_collection("legislator_profiles")
    count = profile_coll.count_documents(query)
    if count == 0:
        raise ValueError(f"ERROR: No profiles found for spec_hash {spec_hash}")

    profiles = profile_coll.find(query)
    data = {
        "detailed_spectrums": [],
        "main_categories": [],
        "primary_categories": [],
        "secondary_categories": [],
    }

    for doc in profiles:
        legislator_id = doc.get("member_id")
        party = doc.get("party")
        member_name = doc.get("name")


        for field in data.keys():
            field_data = doc.get(field, {})
            for name, score in field_data.items():
                data[field].append(
                    {
                        "type": field,
                        "category": name,
                        "score": score["score"],
                        "party": party,
                        "legislator_id": legislator_id,
                        "name": member_name,
                    }
                )

    dfs = {k: pd.DataFrame(v) for k, v in data.items() if v}
    print(f"Found {count} profiles for {spec_hash}")
    return dfs


def compute_rankings(df):
    """
    Compute liberal-to-conservative rankings for each category.
    Liberal = -1, Conservative = 1
    """
    ranked_results = []
    categories = df["category"].unique()

    for category in categories:
        sub = df[df["category"] == category].copy()
        sub = sub.sort_values(by="score", ascending=True).reset_index(drop=True)
        sub["rank"] = np.arange(1, len(sub) + 1)
        sub["percentile_rank"] = sub["rank"] / len(sub)
        ranked_results.append(sub)

    ranked_df = pd.concat(ranked_results, ignore_index=True)
    return ranked_df


def compute_global_rankings(df):
    """
    Compute overall ideological ranking for each legislator across all categories.
    Uses mean score as aggregate ideology index.
    """
    global_df = (
        df.groupby(["legislator_id", "name", "party"], as_index=False)["score"]
        .mean()
        .rename(columns={"score": "mean_score"})
    )
    global_df = global_df.sort_values(by="mean_score", ascending=True).reset_index(drop=True)
    global_df["rank"] = np.arange(1, len(global_df) + 1)
    global_df["percentile_rank"] = global_df["rank"] / len(global_df)
    return global_df


def save_rankings(ranked_df, global_df, name, output_dir):
    """Save rankings to CSV."""
    name_dir = output_dir / sanitize_filename(name)
    name_dir.mkdir(parents=True, exist_ok=True)

    ranked_file = name_dir / "issue_rankings.csv"
    global_file = name_dir / "global_rankings.csv"

    ranked_df.to_csv(ranked_file, index=False)
    global_df.to_csv(global_file, index=False)

    print(f"Saved rankings for {name} to {name_dir}")


def main(args):
    dfs = load_profiles(args.spec_hash)
    output_dir = get_output_dir(args.spec_hash)

    for name, df in dfs.items():
        print(f"Ranking {name} ({len(df)} records)")
        ranked_df = compute_rankings(df)
        global_df = compute_global_rankings(df)
        save_rankings(ranked_df, global_df, name, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        required=True,
        help="REQUIRED: Specify the hash of the profiles you want from legislator_profiles",
    )
    args = parser.parse_args()
    main(args)
