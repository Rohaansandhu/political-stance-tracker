import json
import os
from pathlib import Path
from collections import defaultdict
import pandas as pd

PROFILES_DIR = Path("data/legislator_profiles")
OUTPUT_DIR = Path("data/rankings")
OUTPUT_DIR.mkdir(exist_ok=True)

def load_all_legislator_profiles():
    """Load all legislator profiles from JSON files."""
    legislators = []
    
    if not PROFILES_DIR.exists():
        raise FileNotFoundError(f"Directory {PROFILES_DIR} does not exist")
    
    for profile_file in PROFILES_DIR.glob("*.json"):
        with open(profile_file, 'r', encoding='utf-8') as f:
            profile = json.load(f)
            legislators.append(profile)
    
    print(f"Loaded {len(legislators)} legislator profiles")
    return legislators

def collect_all_categories_and_spectrums(legislators):
    """Collect all unique categories and spectrums across all legislators."""
    all_main_categories = set()
    all_primary_categories = set()
    all_secondary_categories = set()
    all_subcategories = set()
    all_spectrums = set()
    
    for legislator in legislators:
        # Collect categories
        all_main_categories.update(legislator.get("main_categories", {}).keys())
        all_primary_categories.update(legislator.get("primary_categories", {}).keys())
        all_secondary_categories.update(legislator.get("secondary_categories", {}).keys())
        all_subcategories.update(legislator.get("subcategories", {}).keys())
        
        # Collect spectrums
        all_spectrums.update(legislator.get("detailed_spectrums", {}).keys())
    
    return {
        "main_categories": sorted(all_main_categories),
        "primary_categories": sorted(all_primary_categories),
        "secondary_categories": sorted(all_secondary_categories), 
        "subcategories": sorted(all_subcategories),
        "spectrums": sorted(all_spectrums)
    }

def rank_legislators_by_spectrum(legislators, spectrum_name, spectrum_type="detailed_spectrums"):
    """Rank legislators on a specific spectrum/category from left (-1) to right (+1)."""
    
    # Extract scores for this spectrum
    legislator_scores = []
    for legislator in legislators:
        score_dict = legislator.get(spectrum_type, {})
        if spectrum_name in score_dict:
            score = score_dict[spectrum_name]
            legislator_info = {
                "member_id": legislator.get("member_id"),
                "name": legislator.get("name"),
                "party": legislator.get("party"),
                "state": legislator.get("state"),
                "vote_count": legislator.get("vote_count", 0)
            }
            legislator_scores.append((legislator_info, score))
    
    # Sort by score (left to right: -1 to +1)
    legislator_scores.sort(key=lambda x: x[1])
    
    # Add rank information
    ranked_legislators = []
    for rank, (legislator_info, score) in enumerate(legislator_scores, 1):
        ranked_legislators.append((rank, legislator_info, score))
    
    return ranked_legislators

def export_overall_scores_csv(legislators):
    """Export the overall left_right and authoritarian_libertarian scores to CSV."""
    
    csv_dir = OUTPUT_DIR / "csv"
    csv_dir.mkdir(exist_ok=True)

    rows = []
    for legislator in legislators:
        scores = legislator.get("scores", {})
        rows.append({
            "member_id": legislator.get("member_id"),
            "name": legislator.get("name"),
            "party": legislator.get("party"),
            "state": legislator.get("state"),
            "vote_count": legislator.get("vote_count", 0),
            "left_right": round(scores.get("left_right", 0), 4),
            "authoritarian_libertarian": round(scores.get("authoritarian_libertarian", 0), 4)
        })
    
    df = pd.DataFrame(rows)
    csv_file = csv_dir / "overall_scores.csv"
    df.to_csv(csv_file, index=False)
    print(f"Exported overall ideology scores to {csv_file}")

def generate_spectrum_rankings(legislators):
    """Generate rankings for all spectrums and categories."""
    
    # Collect all available categories and spectrums
    all_items = collect_all_categories_and_spectrums(legislators)
    
    rankings = {}
    
    # Rank on detailed spectrums
    for spectrum in all_items["spectrums"]:
        ranked = rank_legislators_by_spectrum(legislators, spectrum, "detailed_spectrums")
        if ranked:  # Only include if we have data
            rankings[f"spectrum_{spectrum}"] = {
                "type": "spectrum",
                "name": spectrum,
                "count": len(ranked),
                "rankings": ranked
            }
            # print(f"{spectrum}: {len(ranked)} legislators")

    # Rank on main categories
    for category in all_items["main_categories"]:
        ranked = rank_legislators_by_spectrum(legislators, category, "main_categories")
        if ranked:
            rankings[f"main_{category}"] = {
                "type": "main_category",
                "name": category,
                "count": len(ranked),
                "rankings": ranked
            }
            # print(f"{category}: {len(ranked)} legislators")
    
    # Rank on primary categories
    for category in all_items["primary_categories"]:
        ranked = rank_legislators_by_spectrum(legislators, category, "primary_categories")
        if ranked:
            rankings[f"primary_{category}"] = {
                "type": "primary_category",
                "name": category,
                "count": len(ranked),
                "rankings": ranked
            }
            # print(f"{category}: {len(ranked)} legislators")
    
    # Rank on secondary categories
    for category in all_items["secondary_categories"]:
        ranked = rank_legislators_by_spectrum(legislators, category, "secondary_categories")
        if ranked:
            rankings[f"secondary_{category}"] = {
                "type": "secondary_category", 
                "name": category,
                "count": len(ranked),
                "rankings": ranked
            }
            # print(f"{category}: {len(ranked)} legislators")
    
    # Rank on subcategories
    for category in all_items["subcategories"]:
        ranked = rank_legislators_by_spectrum(legislators, category, "subcategories")
        if ranked:
            rankings[f"sub_{category}"] = {
                "type": "subcategory",
                "name": category,
                "count": len(ranked),
                "rankings": ranked
            }
            # print(f"{category}: {len(ranked)} legislators")
    
    # Save complete rankings
    rankings_file = OUTPUT_DIR / "all_rankings.json"
    with open(rankings_file, 'w', encoding='utf-8') as f:
        json.dump(rankings, f, indent=2, ensure_ascii=False)
    
    print(f"Saved complete rankings to {rankings_file}")
    return rankings

def create_summary_report(rankings):
    """Create a summary report of the most extreme legislators on each spectrum."""
    summary = {}
    
    for key, ranking_data in rankings.items():
        if not ranking_data["rankings"]:
            continue
            
        name = ranking_data["name"]
        ranked_legislators = ranking_data["rankings"]
        
        # Get most left and most right
        most_left = ranked_legislators[0]  # First (lowest score)
        most_right = ranked_legislators[-1]  # Last (highest score)
        
        summary[name] = {
            "type": ranking_data["type"],
            "total_legislators": len(ranked_legislators),
            "most_left": {
                "rank": most_left[0],
                "legislator": most_left[1],
                "score": most_left[2]
            },
            "most_right": {
                "rank": most_right[0],
                "legislator": most_right[1], 
                "score": most_right[2]
            },
            "range": most_right[2] - most_left[2]
        }
    
    # Save summary
    summary_file = OUTPUT_DIR / "extremes_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"Saved extremes summary to {summary_file}")
    return summary

def create_csv_exports(rankings):
    """Export rankings to CSV files for easier analysis."""
    
    csv_dir = OUTPUT_DIR / "csv"
    csv_dir.mkdir(exist_ok=True)
    
    for key, ranking_data in rankings.items():
        if not ranking_data["rankings"]:
            continue
            
        # Prepare data for CSV
        csv_data = []
        for rank, legislator_info, score in ranking_data["rankings"]:
            csv_data.append({
                "rank": rank,
                "name": legislator_info["name"],
                "member_id": legislator_info["member_id"],
                "party": legislator_info["party"],
                "state": legislator_info["state"],
                "score": round(score, 4),
                "vote_count": legislator_info["vote_count"]
            })
        
        # Save CSV
        df = pd.DataFrame(csv_data)
        csv_filename = f"{ranking_data['type']}_{ranking_data['name'].replace(' ', '_').replace('&', 'and')}.csv"
        csv_file = csv_dir / csv_filename
        df.to_csv(csv_file, index=False)
    
    print(f"Exported CSV files to {csv_dir}")

def print_top_rankings(rankings, top_n=5):
    """Print the top N most left and right legislators for key spectrums."""
    
    key_spectrums = [
        "Government Role",
        "Economic Policy", 
        "Social Policy",
        "Foreign Policy"
    ]
    
    print(f"\n{'='*60}")
    print(f"TOP {top_n} MOST LEFT AND RIGHT LEGISLATORS")
    print(f"{'='*60}")
    
    for spectrum in key_spectrums:
        spectrum_key = f"spectrum_{spectrum}"
        if spectrum_key in rankings:
            ranking_data = rankings[spectrum_key]
            ranked = ranking_data["rankings"]
            
            print(f"\n{spectrum.upper()} SPECTRUM:")
            print("-" * 40)
            
            print(f"Most LEFT ({top_n}):")
            for i in range(min(top_n, len(ranked))):
                rank, info, score = ranked[i]
                print(f"  {rank:2d}. {info['name']} ({info['party']}-{info['state']}) - {score:.3f}")
            
            print(f"Most RIGHT ({top_n}):")
            for i in range(max(0, len(ranked)-top_n), len(ranked)):
                rank, info, score = ranked[i]
                print(f"  {rank:2d}. {info['name']} ({info['party']}-{info['state']}) - {score:.3f}")

def main():
    """Main function to run the ranking analysis."""
    
    try:
        # Load all legislator profiles
        legislators = load_all_legislator_profiles()
        
        if not legislators:
            print("No legislator profiles found!")
            return
        
        # Generate rankings
        rankings = generate_spectrum_rankings(legislators)
        
        # Create summary report
        summary = create_summary_report(rankings)
        
        # Export to CSV
        create_csv_exports(rankings)
        export_overall_scores_csv(legislators)
        
        # Print top rankings for key spectrums
        # print_top_rankings(rankings)
        
        print(f"Analysis complete! Generated rankings for {len(rankings)} spectrums/categories")
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        raise

if __name__ == "__main__":
    raise DeprecationWarning
    main()