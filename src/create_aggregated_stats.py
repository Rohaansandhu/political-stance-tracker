# Script to store aggregated stats per spec_hash per category/spectrum
# Used for quick frontend graph generation
import argparse
import numpy as np
from typing import Dict, List, Any
import db.db_utils as db_utils


def calculate_correlation(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(x) == 0 or len(y) == 0:
        return 0.0

    x_arr = np.array(x)
    y_arr = np.array(y)

    if np.std(x_arr) == 0 or np.std(y_arr) == 0:
        return 0.0

    return float(np.corrcoef(x_arr, y_arr)[0, 1])


def generate_histogram_data(profiles: List[Dict], field: str, category: str) -> Dict:
    """Generate histogram bins and statistics for a given field/category."""

    # Filter profiles that have this category with valid score
    valid_profiles = []
    for p in profiles:
        category_data = p.get(field, {}).get(category)
        # Check that each category has score and the bill_count is 10 or above
        if (
            category_data
            and isinstance(category_data.get("score"), (int, float))
            and category_data.get("bill_count", 0) >= 10
        ):
            valid_profiles.append(p)

    if not valid_profiles:
        return None

    # Create histogram bins from -1 to 1
    bin_size = 0.2
    bins = []

    for i in np.arange(-1.0, 1.0, bin_size):
        bin_start = float(i)
        bin_end = float(i + bin_size)
        bin_label = f"{bin_start:.1f} to {bin_end:.1f}"

        bin_data = {"range": bin_label, "D": 0, "R": 0, "I": 0}

        for profile in valid_profiles:
            score = profile[field][category]["score"]
            if bin_start <= score < bin_end:
                party = profile.get("party", "I")
                bin_data[party] = bin_data.get(party, 0) + 1

        bins.append(bin_data)

    # Calculate statistics by party
    stats = {}
    for party in ["D", "R", "I"]:
        party_profiles = [p for p in valid_profiles if p.get("party") == party]
        scores = [p[field][category]["score"] for p in party_profiles]

        if scores:
            stats[party] = {
                "mean": float(np.mean(scores)),
                "median": float(np.median(scores)),
                "count": len(scores),
                "min": float(np.min(scores)),
                "max": float(np.max(scores)),
                "std": float(np.std(scores)),
            }
        else:
            stats[party] = {
                "mean": None,
                "median": None,
                "count": 0,
                "min": None,
                "max": None,
                "std": None,
            }

    return {
        "chart_type": "histogram",
        "bins": bins,
        "stats": stats,
        "total_count": len(valid_profiles),
    }


def generate_scatter_data(profiles: List[Dict], field: str, category: str) -> Dict:
    """Generate scatter plot data for score vs bill_count."""

    # Filter profiles that have this category with valid score and bill_count
    scatter_data = []
    for p in profiles:
        category_data = p.get(field, {}).get(category)
        if (
            category_data
            and isinstance(category_data.get("score"), (int, float))
            and isinstance(category_data.get("bill_count"), (int, float))
            and category_data.get("bill_count", 0) >= 10
        ):
            scatter_data.append(
                {
                    "member_id": p.get("member_id"),
                    "name": p.get("name"),
                    "party": p.get("party", "I"),
                    "state": p.get("state"),
                    "score": float(category_data["score"]),
                    "bill_count": int(category_data["bill_count"]),
                }
            )

    if not scatter_data:
        return None

    # Calculate correlation
    scores = [d["score"] for d in scatter_data]
    bill_counts = [d["bill_count"] for d in scatter_data]
    correlation = calculate_correlation(scores, bill_counts)

    # Group by party
    by_party = {
        "D": [d for d in scatter_data if d["party"] == "D"],
        "R": [d for d in scatter_data if d["party"] == "R"],
        "I": [d for d in scatter_data if d["party"] == "I"],
    }

    return {
        "chart_type": "scatter",
        "data": scatter_data,
        "by_party": by_party,
        "correlation": float(correlation),
        "total_count": len(scatter_data),
        "score_range": [float(min(scores)), float(max(scores))],
        "bill_count_range": [int(min(bill_counts)), int(max(bill_counts))],
    }


def extract_categories_from_profiles(
    profiles: List[Dict], fields: List[str]
) -> Dict[str, List[str]]:
    """Extract all unique categories for each field from profiles."""
    categories_by_field = {field: set() for field in fields}

    for profile in profiles:
        for field in fields:
            if field in profile and isinstance(profile[field], dict):
                categories_by_field[field].update(profile[field].keys())

    # Convert sets to sorted lists
    return {field: sorted(list(cats)) for field, cats in categories_by_field.items()}


def generate_stats(spec_hash: str) -> List[Dict]:
    """Generate all histogram and scatter stats for a spec_hash."""

    # Fetch all profiles for this spec_hash
    leg_collection = db_utils.get_collection("legislator_profiles")
    profiles = list(leg_collection.find({"spec_hash": spec_hash}))

    if not profiles:
        print(f"No profiles found for spec_hash: {spec_hash}")
        return []

    print(f"Found {len(profiles)} profiles for {spec_hash}")

    # Fields to process
    fields = [
        "detailed_spectrums",
        "main_categories",
    ]

    # Extract all categories for each field
    categories_by_field = extract_categories_from_profiles(profiles, fields)

    all_stats = []

    # Generate stats for each combination
    for field in fields:
        categories = categories_by_field.get(field, [])
        print(f"Processing {field}: {len(categories)} categories")

        for category in categories:
            # Generate histogram data
            histogram_data = generate_histogram_data(profiles, field, category)
            if histogram_data:
                all_stats.append(
                    {
                        "spec_hash": spec_hash,
                        "field": field,
                        "subject": category,
                        "chart_type": "histogram",
                        **histogram_data,
                    }
                )

            # Generate scatter data
            scatter_data = generate_scatter_data(profiles, field, category)
            if scatter_data:
                all_stats.append(
                    {
                        "spec_hash": spec_hash,
                        "field": field,
                        "subject": category,
                        "chart_type": "scatter",
                        **scatter_data,
                    }
                )

    print(f"Generated {len(all_stats)} stat documents for {spec_hash}")
    return all_stats


def find_all_spec_hashes():
    leg_collection = db_utils.get_collection("legislator_profiles")
    return leg_collection.distinct("spec_hash")


def write_stats_to_db(stats: List[Dict]):
    """Write stats to aggregated_stats collection with upsert."""
    if not stats:
        print("No stats to write")
        return

    print(f"Writing {len(stats)} stats to database...")
    for stat in stats:
        # Unique key: spec_hash + field (spectrum vs category) + subject (specific cat/spec) + chart_type
        key_vals = {
            "spec_hash": stat["spec_hash"],
            "field": stat["field"],
            "subject": stat["subject"],
            "chart_type": stat["chart_type"],
        }
        db_utils.update_one("aggregated_stats", stat, key_vals)

    print("Write complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--spec_hash",
        help="Run all category/spectrums for a spec_hash",
    )

    args = parser.parse_args()

    all_stats = []

    if args.spec_hash:
        print(f"Generating stats for {args.spec_hash}")
        stats = generate_stats(args.spec_hash)
        all_stats.extend(stats)
    else:
        print("Generating stats for all spec_hashes")
        spec_hashes = find_all_spec_hashes()
        print(f"Found {len(spec_hashes)} spec_hashes")
        for spec_hash in spec_hashes:
            stats = generate_stats(spec_hash)
            all_stats.extend(stats)

    write_stats_to_db(all_stats)
    print(f"\n Total stats generated: {len(all_stats)}")
