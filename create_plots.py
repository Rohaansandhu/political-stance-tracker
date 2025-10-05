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
    return re.sub(r'[^\w\-_.]', '_', name)

def get_output_dir(model=None, schema=None):
    """Construct and ensure output directory exists."""
    if schema is None:
        schema = SCHEMA_VERSION
    if model is None:
        model = MODEL

    output_dir = Path("data/plots") / f"{model}_schema_v{schema}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def plot_boxplots(df, title, group_name, output_dir):
    """Create boxplots for all groups (spectrums, categories, scores)"""
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
        filename = output_dir / f"box_{safe_group}.png"
        plt.savefig(filename)
        plt.close()
    print(f"Created boxplots for {len(groups)} groups")


def plot_histograms(df, title, group_name, output_dir):
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
        filename = output_dir / f"hist_{safe_group}.png"
        plt.savefig(filename)
        plt.close()
    print(f"Created histograms for {len(groups)} groups")


def load_profiles(model=None, schema=None):
    """Load legislator_profiles collection with appropriate filters"""
    # If model/schema is not specified, set to defaults (determined by env variables)
    if schema is None:
        schema = SCHEMA_VERSION
    if model is None:
        model = MODEL

    # Get profiles
    query = {"model": model, "schema_version": schema}
    profile_coll = db_utils.get_collection("legislator_profiles")
    profiles = profile_coll.find(query)

    # Gather all spectrums, scores, and party
    records = []
    for doc in profiles:
        party = doc.get("party")
        for spectrum, score in doc.get("detailed_spectrums", {}).items():
            records.append({"spectrum": spectrum, "score": score, "party": party})
        # TODO: Add categories (main, primary, secondary, subcategories)

    df = pd.DataFrame(records)
    return df


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

    df = load_profiles(args.model, args.schema)
    output_dir = get_output_dir(args.model, args.schema)

    plot_boxplots(df, "Detailed Spectrums", "spectrum", output_dir)
    plot_histograms(df, "Detailed Spectrums", "spectrum", output_dir)
