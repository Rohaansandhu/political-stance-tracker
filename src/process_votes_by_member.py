import json
from pathlib import Path
import time

from pymongo import UpdateOne
import db.db_utils as db_utils

INPUT_COLLECTION = "rollcall_votes"


def get_legislator_id_map():
    """
    Builds a mapping of all known IDs (Bioguide, LIS, etc.) to the
    canonical 'member_id' stored in the legislators collection.
    This is to handle cases of legislators being both house and senate
    members at different points in time.

    Returns:
        dict: { 'B001234': 'S123', 'S123': 'S123', ... }
    """
    print("Building legislator ID map...")
    id_map = {}
    legislators = db_utils.get_collection("legislators")

    # Fetch only the fields we need to build the map
    projection = {"member_id": 1, "bioguide": 1, "lis": 1, "id": 1}

    for leg in legislators.find({}, projection):
        canonical_id = leg.get("member_id")
        if not canonical_id:
            continue

        # Map the canonical ID to itself
        id_map[canonical_id] = canonical_id

        # Map Bioguide ID to canonical ID
        if bioguide := leg.get("bioguide"):
            id_map[bioguide] = canonical_id

        # Map LIS ID to canonical ID (senate id takes priority)
        if lis := leg.get("lis"):
            id_map[lis] = canonical_id

        # Fallback: check nested 'id' object just in case
        if "id" in leg:
            if b_id := leg["id"].get("bioguide"):
                id_map[b_id] = canonical_id
            if l_id := leg["id"].get("lis"):
                id_map[l_id] = canonical_id

    print(
        f"Mapped {len(id_map)} aliases to {legislators.count_documents({})} legislators."
    )
    return id_map


def write_member_votes_to_db(member_votes):
    """Write member votes to MongoDB 'member_votes' collection"""
    count = 0
    for data in member_votes.values():
        db_utils.update_one("members_with_votes", data, "member_id")
        count += 1

    print(f"Inserted/Updated {count} member vote documents into the database.")


def process_vote_record(vote_data, member_votes, id_map, bulk_actions):
    """Process a single vote record and add to member_votes dict"""
    # Filter out non-bill votes
    if (bill := vote_data.get("bill", {})) == {}:
        return

    # Ignore non-legislative bills
    if bill.get("type") not in {"hr", "hjres", "s", "sjres"}:
        return

    # Only include passage votes
    category = vote_data.get("category")
    if category not in {"passage", "passage-suspension"}:
        return

    # Extract metadata
    vote_id = vote_data.get("vote_id")

    # Process each member's vote
    votes = vote_data.get("votes", {})
    for pos, members in votes.items():
        for member in members:
            # Filter out Vice President votes
            if member == "VP":
                continue

            raw_member_id = member.get("id")
            if not raw_member_id:
                continue

            # RESOLVE ID: Use the map to find the canonical member_id
            # If the ID isn't in our map (shouldn't be possible), fall back to the raw ID.
            member_id = id_map.get(raw_member_id, raw_member_id)

            if member_id not in member_votes:
                member_votes[member_id] = {
                    "member_id": member_id,
                    "name": member.get("display_name"),
                    "party": member.get("party"),
                    "state": member.get("state"),
                }

            # Add this vote to the member_votes collection
            # member_votes collection contains documents for every legislator's individual vote
            vote_obj = {
                "vote_id": vote_id,
                "bill": bill,
                "vote": pos,
                "member_id": member_id,
            }

            bulk_actions.append(
                UpdateOne(
                    {"vote_id": vote_id, "member_id": member_id},
                    {"$set": vote_obj, "$currentDate": {"last_modified": True}},
                    upsert=True,
                )
            )


def process_votes_from_db():
    """Process rollcall votes from MongoDB"""
    member_votes = {}
    bulk_actions = []

    # Get the ID map first from legislators collection
    id_map = get_legislator_id_map()

    rollcall_votes = db_utils.get_collection(INPUT_COLLECTION)
    print(f"Found {rollcall_votes.count_documents({})} rollcall votes in the database")

    rollcall_votes = rollcall_votes.find()
    for vote_data in rollcall_votes:
        process_vote_record(vote_data, member_votes, id_map, bulk_actions)

    # Perform the bulk write for member_votes
    if bulk_actions:
        print(f"Executing {len(bulk_actions)} bulk updates...")
        start_time = time.time()
        db_utils.bulk_write("member_votes", bulk_actions, ordered=False)
        end_time = time.time()
        total_time = end_time - start_time
        print(f"Time taken: {total_time} seconds for {len(bulk_actions)} operations")

    return member_votes


if __name__ == "__main__":

    vote_data = process_votes_from_db()

    write_member_votes_to_db(vote_data)
