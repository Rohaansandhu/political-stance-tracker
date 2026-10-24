import argparse
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import db.db_utils as db_utils
from analysis.definitions import load_axes
from utils.path_utils import get_output_dir, sanitize_filename

PARTY_COLORS = {"D": "blue", "R": "red", "I": "yellow"}


def axis_score_labels():
    """x-axis labels for axis plots: '<-1 pole> (-1)  to  <+1 pole> (+1)'."""
    return {
        axis["id"]: f"{axis['negative_pole']['label']} (-1)  to  {axis['positive_pole']['label']} (+1)"
        for axis in load_axes()
    }


def plot_boxplots(df, title, group_name, output_dir):
    """Create boxplots for all groups (categories, axes)"""
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
            palette=PARTY_COLORS,
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


def plot_histograms(df, title, group_name, output_dir, xlabels=None):
    """Stacked-by-party histograms. `xlabels` optionally maps a group to its x-axis label."""
    xlabels = xlabels or {}
    # Create a subfolder for this category inside output_dir
    category_dir = output_dir / sanitize_filename(title)
    category_dir.mkdir(parents=True, exist_ok=True)

    groups = df[group_name].unique()
    for group in groups:
        sub = df[df[group_name] == group]
        plt.figure(figsize=(6, 4))
        parties = sub["party"].unique()
        plt.hist(
            [sub[sub["party"] == p]["score"] for p in parties],
            bins=np.linspace(-1, 1, 20),
            stacked=True,
            color=[PARTY_COLORS[p] for p in parties],
            label=parties,
        )
        plt.title(f"{title}: {group}")
        plt.xlabel(xlabels.get(group, "Score"))
        plt.ylabel("Count")
        plt.legend()
        plt.tight_layout()

        safe_group = sanitize_filename(group)
        filename = category_dir / f"hist_{safe_group}.png"
        plt.savefig(filename)
        plt.close()
    print(f"Created histograms for {len(groups)} groups")


def plot_party_histograms(df, title, group_name, output_dir, xlabels=None):
    """Within-party view: one histogram panel per party, so each party's spread is visible."""
    xlabels = xlabels or {}
    category_dir = output_dir / sanitize_filename(title)
    category_dir.mkdir(parents=True, exist_ok=True)

    groups = df[group_name].unique()
    for group in groups:
        sub = df[df[group_name] == group]
        parties = [p for p in PARTY_COLORS if p in set(sub["party"])]
        fig, axes = plt.subplots(
            1, len(parties), figsize=(4 * len(parties), 4), sharex=True, sharey=True,
            squeeze=False,
        )
        for ax, party in zip(axes[0], parties):
            ax.hist(
                sub[sub["party"] == party]["score"],
                bins=np.linspace(-1, 1, 20),
                color=PARTY_COLORS[party],
            )
            ax.set_title(party)
            ax.set_xlabel(xlabels.get(group, "Score"), fontsize=7)
        axes[0][0].set_ylabel("Count")
        fig.suptitle(f"{title}: {group}")
        fig.tight_layout()

        filename = category_dir / f"party_hist_{sanitize_filename(group)}.png"
        fig.savefig(filename)
        plt.close(fig)
    print(f"Created per-party histograms for {len(groups)} groups")


def load_profiles(spec_hash):
    """Load legislator_profiles collection and return dict of DataFrames for each score type."""

    query = {"spec_hash": spec_hash}
    profile_coll = db_utils.get_collection("legislator_profiles")
    count = profile_coll.count_documents(query)
    # Raise error if no profiles were found
    if count == 0:
        raise ValueError(f"ERROR: No profiles were found")
    profiles = profile_coll.find(query)

    # Data collectors by field
    data = {
        "primary_categories": [],
        "axes": [],
        # "subcategories": [], Excluding subcategories for now, may remove in future schema versions, lack of precision between models
    }

    for doc in profiles:
        party = doc.get("party")

        # Each key is a dict like { "category_or_axis_name": stats }
        for field in data.keys():
            field_data = doc.get(field, {})
            for name, score in field_data.items():
                data[field].append(
                    {
                        "type": field,
                        "category": name,
                        "score": score["score"],
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

    output_dir = get_output_dir("data/plots", args.spec_hash)
    axis_labels = axis_score_labels()

    for name, df in dfs.items():
        print(f"Plotting {name} ({len(df)} records)")
        title = name.replace("_", " ").title()
        xlabels = axis_labels if name == "axes" else None
        plot_boxplots(df, title, "category", output_dir)
        plot_histograms(df, title, "category", output_dir, xlabels)
        plot_party_histograms(df, title, "category", output_dir, xlabels)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec_hash",
        required=True,
        help="REQUIRED: Specify the hash of the profiles you want from legislator_profiles"
    )

    args = parser.parse_args()
    main(args)
