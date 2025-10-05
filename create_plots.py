import argparse
import re
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from pathlib import Path
from bill_analysis_client import SCHEMA_VERSION
import db_utils
from generate_bill_analysis import MODEL


def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters with underscores."""
    return re.sub(r"[^\w\-_.]", "_", name)


def get_output_dir(model=None, schema=None):
    """Construct and ensure output directory exists."""
    if schema is None:
        schema = SCHEMA_VERSION
    if model is None:
        model = MODEL

    sanitezed_model = sanitize_filename(model)
    output_dir = Path("data/plots") / f"{sanitezed_model}_schema_v{schema}"
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


def load_profiles(model=None, schema=None):
    """Load legislator_profiles collection and return dict of DataFrames for each score type."""
    if schema is None:
        schema = SCHEMA_VERSION
    if model is None:
        model = MODEL

    query = {"model": model, "schema_version": schema}
    profile_coll = db_utils.get_collection("legislator_profiles")
    profiles = profile_coll.find(query)

    # Return an empty list if the query doesn't find anything
    if not profiles:
        return list()

    # Data collectors by category type
    data = {
        "detailed_spectrums": [],
        "main_categories": [],
        "primary_categories": [],
        "secondary_categories": [],
        "subcategories": [],
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

    # # Add combined version if needed
    # all_records = [r for records in data.values() for r in records]
    # dfs["combined"] = pd.DataFrame(all_records)

    return dfs

def main(args):
    """Main Function for creating plots"""

    dfs = load_profiles(args.model, args.schema)
    if not dfs:
        print(f"No profiles found for model: {args.model} and schema v{args.schema}")
        return

    output_dir = get_output_dir(args.model, args.schema)

    for name, df in dfs.items():
        print(f"Plotting {name} ({len(df)} records)")
        plot_boxplots(df, name.replace("_", " ").title(), "category", output_dir)
        plot_histograms(df, name.replace("_", " ").title(), "category", output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        help="Specify the model of the analyses to use (e.g., 'gemini-2.5-flash-lite')",
    )
    parser.add_argument(
        "--schema",
        type=int,
        help="Optionally specify schema version (defaults to latest)",
    )

    args = parser.parse_args()
    main(args)
