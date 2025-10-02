# Script to load roll call votes using usc-run votes, 
# then saves this vote data to the db automatically (unless specified otherwise)
# Vote data must be saved to data/ since that is where the congress scraper outputs with usc-run
import argparse
import subprocess
from load_to_db import load_votes
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load all data from data/ into the database"
    )
    parser.add_argument(
        "--no_db",
        action="store_true",
        help="Don't load the data to the db, just process and save to data/ folder",
    )
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
    no_db = args.no_db
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
    
    # Unless no_db flag is specified, load the votes to MongoDB as well
    if not no_db:
        load_votes()
    
    