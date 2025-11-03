# Script to load roll call votes using usc-run votes,
# then saves this vote data to the db automatically
# Vote data must be saved to data/ since that is where the congress scraper outputs with usc-run
import argparse
import subprocess
import sys
import Path
# Add src/ to import path
SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parent 
sys.path.insert(0, str(SRC_DIR)) 
from db.load_to_db import load_votes

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # usc-run specific arguments
    parser.add_argument(
        "--congress",
        help="Specifies congress",
    )
    parser.add_argument(
        "--session",
        help="Specifies singular session",
    )
    parser.add_argument(
        "--sessions",
        help="Specifies sessions",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Only pull from last 3 days",
    )
    args = parser.parse_args()
    congress = args.congress
    session = args.session
    sessions = args.sessions
    force = args.force
    fast = args.fast
    cmd = [
        "usc-run",
        "votes",
    ]
    if congress:
        cmd += [f"--congress={congress}"]
    if session:
        cmd += [f"--session={session}"]
    if sessions:
        cmd += [f"--sessions={sessions}"]
    if force:
        cmd += ["--force"]
    if fast:
        cmd += ["--fast"]

    # Call usc-run votes with subprocess, this outputs into data/
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)

    load_votes()
