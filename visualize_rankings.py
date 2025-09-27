import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

RESULTS_DIR = Path("data/rankings/csv")
PLOTS_DIR = RESULTS_DIR / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)


def plot_distribution(df, name):
    """Distribution of ideological scores by party."""
    plt.figure(figsize=(8, 6))
    df.boxplot(column="score", by="party", grid=False, showfliers=False)
    plt.title(f"Score Distribution by Party — {name}")
    plt.suptitle("")
    plt.xlabel("Party")
    plt.ylabel("Ideological Score")
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


if __name__ == "__main__":
    csv_files = list(RESULTS_DIR.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError("No CSV files found in data/rankings/csv")

    for csv_file in csv_files:
        df = pd.read_csv(csv_file)
        name = csv_file.stem

        plot_distribution(df, name)
        plot_all_scores(df, name)

        print(f"Plots saved for {csv_file.name} in {PLOTS_DIR}")
