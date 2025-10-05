import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

RESULTS_DIR = Path("data/rankings/csv")
PLOTS_DIR = RESULTS_DIR / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)


def plot_distribution(df, name):
    """Distribution of ideological scores by party."""
    plt.figure(figsize=(8, 6))
    df.boxplot(column="score", by="party", grid=False, showfliers=False, vert=False)
    plt.title(f"Score Distribution by Party — {name}")
    plt.suptitle("")
    plt.xlabel("Ideological Score")
    plt.ylabel("Party")
    plt.savefig(PLOTS_DIR / f"{name}_score_distribution.png", dpi=300)
    plt.close()

def plot_all_scores(df, name):
    """Scatter of rank vs score, colored by party."""
    plt.figure(figsize=(12, 6))
    colors = df["party"].map({"D": "blue", "R": "red", "I": "yellow"}).fillna("gray")

    plt.scatter(df["rank"], df["score"], c=colors, alpha=0.7, s=df["vote_count"])
    plt.xlabel("Rank")
    plt.ylabel("Score")
    plt.title(f"Legislator Scores by Rank — {name}")
    plt.axhline(0, color="black", linestyle="--", linewidth=0.7)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"{name}_rank_vs_score.png", dpi=300)
    plt.close()

def plot_ideology_space(df):
    """Scatter of left-right (x) vs authoritarian-libertarian (y), colored by party."""
    plt.figure(figsize=(8, 8))
    colors = df["party"].map({"D": "blue", "R": "red", "I": "yellow"}).fillna("gray")

    plt.scatter(
        df["left_right"], 
        df["authoritarian_libertarian"], 
        c=colors, 
        alpha=0.7, 
        s=df["vote_count"]
    )
    plt.axhline(0, color="black", linestyle="--", linewidth=0.7)
    plt.axvline(0, color="black", linestyle="--", linewidth=0.7)
    plt.xlabel("Left  <----  Left-Right Score  ---->  Right")
    plt.ylabel("Libertarian  <----  Libertarian-Authoritarian Score  ---->  Authoritarian")
    plt.title("Overall Ideological Space of Legislators")
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "overall_scores_ideology_space.png", dpi=300)
    plt.close()

if __name__ == "__main__":
    raise DeprecationWarning
    csv_files = list(RESULTS_DIR.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError("No CSV files found in data/rankings/csv")

    for csv_file in csv_files:
        df = pd.read_csv(csv_file)
        name = csv_file.stem

        if csv_file.name == "overall_scores.csv":
            plot_ideology_space(df) 
        else:
            plot_distribution(df, name)
            plot_all_scores(df, name)

        print(f"Plots saved for {csv_file.name} in {PLOTS_DIR}")
    
