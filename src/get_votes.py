# Script to load roll call votes, then saves this vote data to the db automatically
# Vote data must be saved to data/ since that is where the congress scraper outputs
#
# House: clerk.house.gov no longer serves the vote index pages that `usc-run votes` relies on,
# but individual roll call files still download. So new House votes are found by trying roll
# numbers in order until the clerk reports none available.
# Senate: goes through `usc-run votes` unchanged. senate.gov currently blocks automated
# clients (403), so this path fails until that changes.
import argparse
import subprocess
from pathlib import Path

from congress.tasks import utils as congress_utils
from congress.tasks import vote_info
from db.load_to_db import load_votes

DATA_DIR = Path("data")

# Stop probing past this roll number, in case the clerk's "missing roll" signal changes
MAX_HOUSE_ROLL = 5000

# What clerk.house.gov serves (with a 200) for a roll call number that doesn't exist yet
MISSING_ROLL_MARKER = b"Error sanitizing file"


def local_house_vote_numbers(congress, session):
    """Roll numbers of House votes already downloaded for a congress and session."""
    votes_dir = DATA_DIR / str(congress) / "votes" / str(session)
    return {
        int(folder.name[1:])
        for folder in votes_dir.glob("h[0-9]*")
        if (folder / "data.json").exists()
    }


def download_house_roll(congress, session, number):
    """
    Download one roll call XML into the congress scraper's cache.
    Returns False once `number` is past the last published roll call.
    """
    url = f"https://clerk.house.gov/evs/{session}/roll{number:03d}.xml"
    # Same cache path vote_info.fetch_vote reads, so it won't download the file again.
    # force bypasses any stale cached copy.
    cache_path = f"{congress}/votes/{session}/h{number}/h{number}.xml"
    body = congress_utils.download(url, cache_path, {"force": True, "binary": True})
    return bool(body) and MISSING_ROLL_MARKER not in body


def fetch_new_house_votes(congress, session, force=False):
    """
    Download House roll calls for a session that we don't have yet, including gaps below
    the highest one we do have, until the clerk has no more. With `force`, re-download all.
    Returns the number of votes saved.
    """
    local = set() if force else local_house_vote_numbers(congress, session)
    last_local = max(local, default=0)
    saved = 0

    print(f"House {congress}.{session}: fetching rolls missing locally (have {len(local)})")
    for number in range(1, MAX_HOUSE_ROLL + 1):
        if number in local:
            continue

        if not download_house_roll(congress, session, number):
            if number > last_local:
                print(f"Roll {number} not published yet, stopping")
                break
            print(f"Roll {number}: not available, skipping")
            continue

        vote_id = f"h{number}-{congress}.{session}"
        result = vote_info.fetch_vote(vote_id, {})
        if result.get("saved"):
            saved += 1
        else:
            # e.g. a vacated vote: nothing saved, but later rolls may exist
            print(f"Roll {number}: skipped ({result.get('reason')})")

    print(f"House {congress}.{session}: saved {saved} new votes")
    return saved


def house_sessions(congress, session):
    """The session year(s) to fetch: the one given, else every started year of the congress."""
    if session:
        return [session]
    first_year = congress_utils.get_congress_first_year(congress)
    return [str(y) for y in (first_year, first_year + 1) if y <= congress_utils.current_legislative_year()]


def run_usc_votes(args):
    """Fetch votes with the congress scraper's own `usc-run votes` task."""
    cmd = ["usc-run", "votes", "--chamber=senate"]
    if args.congress:
        cmd += [f"--congress={args.congress}"]
    if args.session:
        cmd += [f"--session={args.session}"]
    if args.sessions:
        cmd += [f"--sessions={args.sessions}"]
    if args.force:
        cmd += ["--force"]
    if args.fast:
        cmd += ["--fast"]

    # Call usc-run votes with subprocess, this outputs into data/
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:", e)
        print(e.stderr[-1000:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chamber",
        choices=["house", "senate"],
        default="house",
        help="Chamber to fetch (default: house)",
    )
    parser.add_argument(
        "--congress",
        help="Specifies congress (default: current congress)",
    )
    parser.add_argument(
        "--session",
        help="Specifies singular session (default: every started session of the congress)",
    )
    # Senate only (usc-run)
    parser.add_argument(
        "--sessions",
        help="Specifies sessions (senate only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Only pull from last 3 days (senate only; house always fetches just new votes)",
    )
    args = parser.parse_args()

    if args.chamber == "house":
        congress = int(args.congress or congress_utils.current_congress())
        for session in house_sessions(congress, args.session):
            fetch_new_house_votes(congress, session, args.force)
    else:
        run_usc_votes(args)

    load_votes()
