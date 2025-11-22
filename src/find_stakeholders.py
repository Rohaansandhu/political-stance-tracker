import db.db_utils as db_utils
import argparse
from pymongo import UpdateOne
from calc_member_ideology import (
    get_vote_value,
    load_bill_analyses_from_db,
    check_inputs,
    build_bill_id,
    get_spec_hash,
)


def find_stakeholders(bill_analyses, chamber, spec_hash):
    legislator_stakeholders = []

    member_votes_col = db_utils.get_collection("member_votes")

    for legislator_data in member_votes_col.find():
        # Senator's member_id looks like SXXX (Ex: S313), House uses bioguide which has 7 chars
        if chamber == "house":
            if len(legislator_data["member_id"]) <= 4:
                continue
        elif chamber == "senate":
            if len(legislator_data["member_id"]) > 4:
                continue

        stakeholder_freq = {}

        for vote in legislator_data["votes"]:

            bill_id = build_bill_id(vote["bill"])

            if bill_id not in bill_analyses:
                continue

            vote_value = get_vote_value(vote["vote"])
            stakeholders = []
            if vote_value == 1:
                stakeholders = bill_analyses[bill_id]["voting_analysis"]["yes_vote"][
                    "stakeholder_support"
                ]
            elif vote_value == -1:
                stakeholders = bill_analyses[bill_id]["voting_analysis"]["no_vote"][
                    "stakeholder_support"
                ]
            # no vote on this bill
            else:
                continue

            for stakeholder in stakeholders:
                if isinstance(stakeholder, str):
                    stakeholder_freq[stakeholder] = (
                        stakeholder_freq.get(stakeholder, 0) + 1
                    )

        # Filter out items that only have a count of 1, likely too specific to include
        # Also, the document is too large without filtering
        # TODO: Combine similar stakeholders (slight wording differences)
        filtered_freq = {k: v for k, v in stakeholder_freq.items() if v > 1}

        if filtered_freq:
            filtered_freq["member_id"] = legislator_data["member_id"]
            filtered_freq["spec_hash"] = spec_hash
            legislator_stakeholders.append(filtered_freq)

    return legislator_stakeholders


def write_stakeholders_to_db(legislators):
    """Write legislator profiles to MongoDB collection."""
    count = 0
    actions = []
    for profile in legislators:
        query = {
            "member_id": profile["member_id"],
            "spec_hash": profile["spec_hash"],
        }
        actions.append(
            UpdateOne(
                query,
                {"$set": profile, "$currentDate": {"last_modified": True}},
                upsert=True,
            )
        )
        count += 1

    if actions:
        db_utils.bulk_write("legislator_stakeholders", actions)
    print(f"Updated stakeholders for {count} members")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Required args
    parser.add_argument(
        "--model",
        required=True,
        help="Specify the model of the analyses to use (e.g., 'gemini-2.5-flash-lite')",
    )
    parser.add_argument(
        "--schema",
        type=int,
        help="Optionally specify schema version (defaults to latest)",
    )
    # Optional paramaters
    parser.add_argument(
        "--congress",
        type=int,
        help="Pass a specific congress to generate plots for (defaults to all data)",
    )
    parser.add_argument(
        "--chamber", help="Specify the chamber of the analyses to use (house, senate)"
    )
    parser.add_argument(
        "--bill_type", nargs="+", help="Specify the bill type(s) to use "
    )

    args = parser.parse_args()

    # Check inputs
    check_inputs(args.model, args.schema, args.congress, args.chamber, args.bill_type)

    # Load bill analyses for specific model
    bill_analyses = load_bill_analyses_from_db(
        args.model, args.schema, args.congress, args.bill_type
    )

    spec_hash = get_spec_hash(
        args.model, args.schema, args.congress, args.chamber, args.bill_type
    )

    legislator_stakeholders = find_stakeholders(bill_analyses, args.chamber, spec_hash)

    if legislator_stakeholders:
        write_stakeholders_to_db(legislator_stakeholders)
