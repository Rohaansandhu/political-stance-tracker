import argparse
import re
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from pathlib import Path
import db_utils


def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters with underscores."""
    return re.sub(r"[^\w\-_.]", "_", name)


def get_output_dir(spec_hash):
    """Construct and ensure output directory exists."""

    sanitezed_hash = sanitize_filename(spec_hash)
    output_dir = Path("data/plots") / sanitezed_hash
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def plot_boxplots(df, title, group_name, output_dir):
    """Create boxplots for all groups (spectrums, categories, scores)"""
    # Create a subfolder for this category inside output_dir
    category_dir = output_dir / sanitize_filename(title)
    category_dir.mkdir(parents=True, exist_ok=True)

    groups = df[group_name].unique()
    for group in groups:
        sub = df[df[group_name] == group]
        plt.figure(figsize=(6, 4))
        sns.boxplot(
            data=sub,
            x="party",
            y="score",
            hue="party",
            palette={"D": "blue", "R": "red", "I": "yellow"},
            legend=False,
        )
        plt.title(f"{title}: {group}")
        plt.ylabel("Score")
        plt.tight_layout()

        safe_group = sanitize_filename(group)
        filename = category_dir / f"box_{safe_group}.png"
        plt.savefig(filename)
        plt.close()
    print(f"Created boxplots for {len(groups)} groups")


def plot_histograms(df, title, group_name, output_dir):
    # Create a subfolder for this category inside output_dir
    category_dir = output_dir / sanitize_filename(title)
    category_dir.mkdir(parents=True, exist_ok=True)

    groups = df[group_name].unique()
    for group in groups:
        sub = df[df[group_name] == group]
        plt.figure(figsize=(6, 4))
        parties = sub["party"].unique()
        party_colors = {"D": "blue", "R": "red", "I": "yellow"}
        plt.hist(
            [sub[sub["party"] == p]["score"] for p in parties],
            bins=np.linspace(-1, 1, 20),
            stacked=True,
            color=[party_colors[p] for p in parties],
            label=parties,
        )
        plt.title(f"{title}: {group}")
        plt.xlabel("Score")
        plt.ylabel("Count")
        plt.legend()
        plt.tight_layout()

        safe_group = sanitize_filename(group)
        filename = category_dir / f"hist_{safe_group}.png"
        plt.savefig(filename)
        plt.close()
    print(f"Created histograms for {len(groups)} groups")


def load_profiles(spec_hash):
    """Load legislator_profiles collection and return dict of DataFrames for each score type."""

    query = {"spec_hash": spec_hash}
    profile_coll = db_utils.get_collection("legislator_profiles")
    count = profile_coll.count_documents(query)
    # Raise error if no profiles were found
    if count == 0:
        return ValueError(f"ERROR: No profiles were found")
    profiles = profile_coll.find(query)


    # Data collectors by category type
    data = {
        "detailed_spectrums": [],
        "main_categories": [],
        "primary_categories": [],
        "secondary_categories": [],
        # "subcategories": [], Excluding subcategories for now, may remove in future schema versions, lack of precision between models
    }

    for doc in profiles:
        party = doc.get("party")

        # Each key is a dict like { "category_name": score_value }
        for field in data.keys():
            field_data = doc.get(field, {})
            for name, score in field_data.items():
                data[field].append(
                    {
                        "type": field,
                        "category": name,
                        "score": score,
                        "party": party,
                    }
                )

    # Convert to individual DataFrames
    dfs = {k: pd.DataFrame(v) for k, v in data.items() if v}

    print(f"Found {count} profiles for {spec_hash}")
    return dfs

def main(args):
    """Main Function for creating plots"""

    dfs = load_profiles(args.spec_hash)

    output_dir = get_output_dir(args.spec_hash)

    for name, df in dfs.items():
        print(f"Plotting {name} ({len(df)} records)")
        plot_boxplots(df, name.replace("_", " ").title(), "category", output_dir)
        plot_histograms(df, name.replace("_", " ").title(), "category", output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        required=True,
        help="REQUIRED: Specify the hash of the profiles you want from legislator_profiles"
    )

    args = parser.parse_args()
    main(args)
