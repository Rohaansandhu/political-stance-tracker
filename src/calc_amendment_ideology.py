"""
Legislator profiles that use amendment votes, under new spec_hashes.

  --votes amendments   amendment votes only (validation against passage-only profiles)
  --votes combined     passage and amendment votes together (candidate site replacement)

Amendment member votes are built in memory from rollcall_votes and never written to
member_votes. Everything else reuses calc_member_ideology unchanged.

Usage:
    python3 src/calc_amendment_ideology.py --model gpt-6-luna --chamber house --votes combined
"""

import argparse
from collections import defaultdict

from analysis.amendment_analysis_client import AMENDMENT_SCHEMA_VERSION
import calc_member_ideology as cmi
from calc_institutional import calculate_institutional_scores
import db.db_utils as db_utils
from process_votes_by_member import get_legislator_id_map

AMENDMENT_ANALYSES = "amendment_analyses"
VOTE_SETS = {"amendments": "only", "combined": "combined"}


def get_amendment_spec_hash(base_spec_hash, amendment_schema, vote_set):
    """'..._all_house_all' -> '..._all_house_all_amd1-combined'"""
    return f"{base_spec_hash}_amd{amendment_schema}-{VOTE_SETS[vote_set]}"


def load_amendment_analyses(model, amendment_schema=None, congress=None, bill_type=None):
    """Substantive amendment analyses, keyed by amendment_id (parent bill filters apply)."""
    if amendment_schema is None:
        amendment_schema = AMENDMENT_SCHEMA_VERSION
    query = {
        "model": model,
        "amendment_schema_version": amendment_schema,
        "kind": "substantive",
    }
    if congress:
        query["congress"] = str(congress)
    if bill_type:
        query["bill_type"] = {"$in": bill_type}

    analyses = {}
    for doc in db_utils.get_collection(AMENDMENT_ANALYSES).find(query):
        analyses.setdefault(doc["amendment_id"], doc)
    print(
        f"Loaded {len(analyses)} substantive amendment analyses for model '{model}' "
        f"(amendment schema v{amendment_schema})"
    )
    return analyses


def matched_vote_ids(amendment_ids):
    """
    {vote_id: amendment doc} for agree-to (+1) roll calls on the given amendments.
    get_amendments.py already drops roll calls shared by several amendments (en bloc).
    """
    votes = {}
    query = {"amendment_id": {"$in": sorted(amendment_ids)}}
    for amendment in db_utils.get_collection("amendments").find(query):
        for entry in amendment.get("votes", []):
            if entry.get("vote_direction") == 1:
                votes[entry["vote_id"]] = amendment
    return votes


def amendment_vote_records(rollcall, amendment, id_map):
    """
    One member vote record per member on an amendment roll call, shaped like a member_votes
    doc. The `bill` is the amendment, so build_bill_id gives the amendment_id.
    """
    bill = {
        "type": amendment["type"],
        "number": amendment["number"],
        "congress": int(amendment["congress"]),
    }
    records = []
    for pos, members in (rollcall.get("votes") or {}).items():
        for member in members:
            # Same filtering as process_votes_by_member.process_vote_record
            if member == "VP":
                continue
            raw_member_id = member.get("id")
            if not raw_member_id:
                continue
            member_id = id_map.get(raw_member_id, raw_member_id)
            records.append(
                {
                    "vote_id": rollcall["vote_id"],
                    "bill": bill,
                    "vote": pos,
                    "member_id": member_id,
                    "date": rollcall.get("date"),
                }
            )
    return records


def build_amendment_votes_by_member(amendment_ids, id_map=None):
    """{member_id: [vote records]} for every matched +1 roll call on the given amendments."""
    id_map = id_map if id_map is not None else get_legislator_id_map()
    by_vote = matched_vote_ids(amendment_ids)

    votes_by_member = defaultdict(list)
    query = {"vote_id": {"$in": sorted(by_vote)}}
    for rollcall in db_utils.get_collection("rollcall_votes").find(query):
        for record in amendment_vote_records(rollcall, by_vote[rollcall["vote_id"]], id_map):
            votes_by_member[record["member_id"]].append(record)

    total = sum(len(v) for v in votes_by_member.values())
    print(f"Built {total} amendment member votes from {len(by_vote)} roll calls")
    return dict(votes_by_member)


def build_profiles(args):
    """Compute the profiles for one spec. Returns (spec_hash, profiles)."""
    amendment_analyses = load_amendment_analyses(
        args.model, args.amendment_schema, args.congress, args.bill_type
    )
    if not amendment_analyses:
        raise SystemExit("No substantive amendment analyses found for these filters")

    if args.votes == "amendments":
        bill_analyses = dict(amendment_analyses)
        axis_analyses = dict(amendment_analyses)
        include_db_votes = False
    else:
        # Keys can't collide: amendment ids start with hamdt/samdt
        bill_analyses = {
            **cmi.load_bill_analyses_from_db(args.model, args.schema, args.congress, args.bill_type),
            **amendment_analyses,
        }
        axis_analyses = {
            **cmi.load_axis_analyses_from_db(
                args.model, args.axis_schema, args.congress, args.bill_type
            ),
            **amendment_analyses,
        }
        include_db_votes = True

    extra_votes = build_amendment_votes_by_member(amendment_analyses)

    base_spec_hash = cmi.get_spec_hash(
        args.model, args.schema, args.congress, args.chamber, args.bill_type, args.axis_schema
    )
    amendment_schema = args.amendment_schema or AMENDMENT_SCHEMA_VERSION
    spec_hash = get_amendment_spec_hash(base_spec_hash, amendment_schema, args.votes)
    print(f"spec_hash: {spec_hash}")

    institutional_scores = calculate_institutional_scores(args.congress, args.chamber)
    profiles = cmi.process_all_legislators(
        bill_analyses,
        args.model,
        spec_hash,
        args.schema,
        args.chamber,
        axis_analyses,
        args.axis_schema,
        institutional_scores,
        extra_votes_by_member=extra_votes,
        include_db_votes=include_db_votes,
    )

    profiles = cmi.apply_bayesian_weighting(profiles)
    profiles = cmi.apply_bayesian_weighting(profiles, "axes", skip={cmi.INSTITUTIONAL})
    profiles = cmi.add_within_party_z(profiles)
    profiles = cmi.generate_rankings(profiles)

    for profile in profiles:
        profile["amendment_schema_version"] = amendment_schema
        profile["vote_set"] = args.votes
    return spec_hash, profiles


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", required=True, help="Model of the analyses to use")
    parser.add_argument("--schema", type=int, help="Category schema version (default: latest)")
    parser.add_argument("--axis_schema", type=int, help="Axis schema version (default: latest)")
    parser.add_argument(
        "--amendment_schema", type=int, help="Amendment schema version (default: latest)"
    )
    parser.add_argument("--congress", type=int, help="Only this congress")
    parser.add_argument("--chamber", help="house or senate")
    parser.add_argument("--bill_type", nargs="+", help="Parent bill type(s) to use")
    parser.add_argument(
        "--votes",
        required=True,
        choices=sorted(VOTE_SETS),
        help="amendments: amendment votes only; combined: passage + amendment votes",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    cmi.check_inputs(
        cli_args.model, cli_args.schema, cli_args.congress, cli_args.chamber, cli_args.bill_type
    )
    _, built_profiles = build_profiles(cli_args)
    cmi.write_profiles_to_db(built_profiles)
