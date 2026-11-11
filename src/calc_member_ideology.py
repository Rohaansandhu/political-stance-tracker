import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from analysis.axis_analysis_client import AXIS_SCHEMA_VERSION
from analysis.bill_analysis_client import SCHEMA_VERSION
import db.db_utils as db_utils
from calc_institutional import calculate_institutional_scores, caucus_party
from process_votes_by_member import get_legislator_id_map
from pymongo import UpdateOne

from utils.agreement_utils import (
    AgreementAccumulator,
    compute_shrink_k,
    contest_weight,
    finalize,
)
from utils.stat_utils import within_group_zscores
from utils.vote_utils import build_bill_id, get_vote_value


DATA_DIR = Path("data")
MEMBER_VOTES_DIR = Path("data/organized_votes")
INPUT_COLLECTION = "bill_analyses"
OUTPUT_DIR = Path("data/legislator_profiles")
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT_COLLECTION = "legislator_profiles"
AXIS_INPUT_COLLECTION = "bill_axis_analyses"

# left_right is derived from category analyses, institutional is computed from roll calls
LEFT_RIGHT = "left_right"
INSTITUTIONAL = "institutional"

# Profile fields of {name: {score, bill_count}} that get weighted and ranked
RANKED_FIELDS = ("primary_categories", "axes")

PARTIES = {"D", "R"}
# Roll call vote_ids start with the chamber's letter ("h191-118.2024", "s12-119.2025")
CHAMBER_CODES = {"house": "h", "senate": "s"}
MIN_BILLS_FOR_Z = 10

# Roll calls that member votes are built from (see process_votes_by_member.py)
CONTEST_CATEGORIES = ["passage", "passage-suspension"]
# Temporary profile key holding per-member agreement sums until apply_agreement runs
AGREEMENT_SUMS_KEY = "_agreement_sums"
# Temporary profile key holding the sums behind party-relative scores until
# apply_party_relative runs
PARTY_SUMS_KEY = "_party_relative_sums"

_missing_contest_warned = False


def extract_top_votes(votes, n=3):
    """
    Return the top N conservative and top N liberal actions for a category,
    ranked strictly by their weighted impact on the legislator's final score.
    """
    # Sort strictly by weighted_score (Most negative to most positive)
    sorted_votes = sorted(votes, key=lambda v: v["weighted_score"])

    def clean(vote):
        return {
            "bill_id": vote["bill_id"],
            "vote": vote["vote"],
            "date": vote["date"],
            "score_impact": round(vote["weighted_score"], 3), 
        }

    # Filter out 0-impact or opposite-impact votes before slicing.
    # This prevents a moderately liberal vote from being labeled "Most Conservative" 
    # just because a member has literally zero conservative votes.
    conservative_actions = [v for v in sorted_votes if v["weighted_score"] > 0]
    liberal_actions = [v for v in sorted_votes if v["weighted_score"] < 0]

    return {
        # Grab the highest positive scores (end of the list, reversed)
        "conservative": [clean(v) for v in conservative_actions[-n:][::-1]],
        # Grab the lowest negative scores (start of the list)
        "liberal": [clean(v) for v in liberal_actions[:n]],
    }


def extract_recent_votes(all_votes_with_category, n=5, threshold=0.1):
    """
    Return the N most recent high-impact votes overall.
    Threshold uses weighted_score, so it filters out low-impact or highly-bipartisan votes.
    """
    conservative = sorted(
        [v for v in all_votes_with_category if v["weighted_score"] > threshold],
        key=lambda v: v.get("date", ""),
        reverse=True,
    )[:n]

    liberal = sorted(
        [v for v in all_votes_with_category if v["weighted_score"] < -threshold],
        key=lambda v: v.get("date", ""),
        reverse=True,
    )[:n]

    def clean(vote):
        return {
            "bill_id": vote["bill_id"],
            "vote": vote["vote"],
            "date": vote["date"],
            "score_impact": round(vote["weighted_score"], 3),
            "category": vote["category"],
        }

    return {
        "conservative": [clean(v) for v in conservative],
        "liberal": [clean(v) for v in liberal],
    }


def calculate_average_scores(vote_data_dict):
    """ "Helper function to calculate average scores for each category given a dict of votes per category."""
    results = {}

    for category, votes in vote_data_dict.items():
        if not votes:
            continue

        avg_score = round(sum(v["weighted_score"] for v in votes) / len(votes), 3)

        results[category] = {
            "score": avg_score,
            "bill_count": len(votes),
        }

    return results


def calculate_party_sums(vote_data_dict):
    """
    Per name, the sums behind the party-relative score: the member's summed weighted score
    (`ws`), what their caucus's average votes would have summed to (`base`), and the count.
    """
    return {
        name: {
            "ws": sum(v["weighted_score"] for v in votes),
            "base": sum(v["base"] for v in votes),
            "n": len(votes),
        }
        for name, votes in vote_data_dict.items()
        if votes
    }


def calculate_agreement_sums(vote_data_dict):
    """
    Per name, the sums the agreement index is built from (num, den, vote_count) plus the
    per-vote weights used to set the shrinkage constant. Votes with no contest weight are skipped.
    """
    results = {}

    for name, votes in vote_data_dict.items():
        acc = AgreementAccumulator()
        for v in votes:
            acc.add(v.get("contest"), v["weighted_score"])
        results[name] = acc.as_sums()

    return results


def rollcall_query(congress=None, chamber=None, vote_ids=None):
    """
    Passage roll calls with the same congress/chamber filters as calc_institutional. With
    `vote_ids`, exactly those roll calls instead, whatever their category (amendment votes).
    """
    if vote_ids is not None:
        return {"vote_id": {"$in": sorted(vote_ids)}}
    query = {"category": {"$in": CONTEST_CATEGORIES}}
    if congress:
        query["congress"] = congress
    if chamber:
        query["chamber"] = CHAMBER_CODES[chamber]
    return query


def load_contest_weights(congress=None, chamber=None, vote_ids=None):
    """
    Contest weight (0 unanimous .. 1 tied) of every roll call `rollcall_query` selects,
    keyed by vote_id.
    """
    query = rollcall_query(congress, chamber, vote_ids)

    weights = {}
    for vote in db_utils.get_collection("rollcall_votes").find(
        query, {"vote_id": 1, "votes": 1}
    ):
        counts = {1: 0, -1: 0}
        for vote_key, voters in vote.get("votes", {}).items():
            value = get_vote_value(vote_key)
            if value == 0:
                continue
            # "VP" entries are plain strings, not voters
            counts[value] += sum(1 for voter in voters if isinstance(voter, dict))
        weights[vote["vote_id"]] = contest_weight(counts[1], counts[-1])

    print(f"Loaded contest weights for {len(weights)} roll calls")
    return weights


def party_vote_means(rollcall, caucus_of_voter):
    """
    Each caucus's average vote on one roll call, from +1 (all Yea) to -1 (all Nay), keyed
    "D"/"R". `caucus_of_voter(voter)` gives a voter's caucus on that roll call, or None.
    """
    tallies = defaultdict(lambda: [0, 0])
    for vote_key, voters in (rollcall.get("votes") or {}).items():
        value = get_vote_value(vote_key)
        if value == 0:
            continue
        for voter in voters:
            # "VP" entries are plain strings, not voters
            if not isinstance(voter, dict):
                continue
            caucus = caucus_of_voter(voter)
            if caucus in PARTIES:
                tallies[caucus][0 if value == 1 else 1] += 1
    return {
        caucus: (yea - nay) / (yea + nay)
        for caucus, (yea, nay) in tallies.items()
        if yea + nay
    }


def load_party_vote_means(congress=None, chamber=None, vote_ids=None, caucus_parties=None):
    """
    {vote_id: {"D": mean vote, "R": mean vote}} for the roll calls `rollcall_query` selects.
    Voters count with the party on the roll call; independents with the party they caucus with.
    """
    caucus_parties = caucus_parties if caucus_parties is not None else load_caucus_parties()
    id_map = get_legislator_id_map()

    def caucus_of_voter(voter):
        party = voter.get("party")
        if party in PARTIES:
            return party
        member_id = id_map.get(voter.get("id"), voter.get("id"))
        return caucus_parties.get(member_id)

    means = {}
    for rollcall in db_utils.get_collection("rollcall_votes").find(
        rollcall_query(congress, chamber, vote_ids), {"vote_id": 1, "votes": 1}
    ):
        means[rollcall["vote_id"]] = party_vote_means(rollcall, caucus_of_voter)

    print(f"Loaded party vote means for {len(means)} roll calls")
    return means


def check_inputs(model, schema, congress, chamber, bill_type):
    if congress and congress < 113:
        raise ValueError(f"Congress must be at least 113, not {congress}")
    if chamber and chamber not in {"house", "senate"}:
        raise ValueError(f"Chamber must be (house, senate) not {chamber}")
    if bill_type:
        for bt in bill_type:
            if bt not in {"hr", "hjres", "s", "sjres"}:
                raise ValueError(f"Bill types must be (hr, hjres, s, sjres) not {bt}")


def collect_category_votes(
    political_categories,
    bill_id,
    vote,
    date,
    vote_value,
    primary_votes,
    sub_votes,
    contest=None,
    party_mean=None,
):
    """
    Add one vote's weighted score to each category/subcategory the bill was tagged with.
    Returns the weighted scores added to primary categories (used to derive left_right).

    `party_mean` is the member's caucus's average vote on this roll call (-1..+1). Each
    primary record then also carries `base`: what the vote would have scored had the member
    voted exactly like their caucus's average.
    """
    primary_scores = []

    for primary_category in political_categories.get("primary_categories", []):
        if isinstance(primary_category, dict) and primary_category.get("name"):
            category_name = primary_category.get("name", "")
            partisan_score = primary_category.get("partisan_score", 0)
            impact_score = primary_category.get("impact_score", 0)
            weighted_score = partisan_score * impact_score * vote_value

            if partisan_score != 0:
                record = {
                    "bill_id": bill_id,
                    "weighted_score": weighted_score,
                    "vote": vote,
                    "date": date,
                    "contest": contest,
                }
                if party_mean is not None:
                    record["base"] = partisan_score * impact_score * party_mean
                primary_votes[category_name].append(record)
                primary_scores.append(weighted_score)

    for subcategory in political_categories.get("subcategories", []):
        if isinstance(subcategory, dict) and subcategory.get("name"):
            category_name = subcategory.get("name", "")
            partisan_score = subcategory.get("partisan_score", 0)
            impact_score = subcategory.get("impact_score", 0)
            weighted_score = partisan_score * impact_score * vote_value

            if partisan_score != 0:
                sub_votes[category_name].append(
                    {
                        "bill_id": bill_id,
                        "weighted_score": weighted_score,
                    }
                )

    return primary_scores


def collect_axis_votes(
    axis_analysis,
    primary_names,
    bill_id,
    vote_value,
    axis_votes,
    axis_category_votes,
    contest=None,
):
    """
    Add one vote's weighted score to each axis the bill was scored on, and to that axis's
    breakdown for each primary category the bill is tagged with.
    """
    for axis_score in axis_analysis.get("axis_scores", []):
        position = axis_score.get("position", 0)
        relevance = axis_score.get("relevance", 0)
        if position == 0 or relevance == 0:
            continue

        axis = axis_score["axis"]
        record = {
            "bill_id": bill_id,
            "weighted_score": position * relevance * vote_value,
            "contest": contest,
        }
        axis_votes[axis].append(record)
        for category_name in primary_names:
            axis_category_votes[axis][category_name].append(record)


def build_axis_results(axis_votes, axis_category_votes):
    """Average each axis, with its per-category breakdown."""
    results = calculate_average_scores(axis_votes)
    for axis, stats in results.items():
        stats["by_category"] = calculate_average_scores(axis_category_votes[axis])
    return results


def build_left_right_result(left_right_votes, primary_category_results):
    """
    left_right is derived from the category analyses: each bill's primary-category
    scores averaged together. Its per-category breakdown is the category scores themselves.
    """
    result = calculate_average_scores({LEFT_RIGHT: left_right_votes}).get(LEFT_RIGHT)
    if result is None:
        return None

    result["by_category"] = {
        category: {"score": stats["score"], "bill_count": stats["bill_count"]}
        for category, stats in primary_category_results.items()
    }
    return result


def calculate_legislator_ideology(
    legislator_votes,
    bill_analyses,
    axis_analyses=None,
    contest_weights=None,
    party_means=None,
    caucus=None,
):
    """
    Score one legislator's votes. With `contest_weights` (vote_id -> contest weight) the
    result also carries the sums behind the agreement index under `_agreement_sums`.
    With `party_means` (vote_id -> {caucus: mean vote}) and the member's `caucus`, it also
    carries the sums behind the party-relative score under `_party_relative_sums`.
    """
    party_relative = party_means is not None and caucus in PARTIES
    global _missing_contest_warned
    axis_analyses = axis_analyses or {}

    primary_category_votes = defaultdict(list)
    subcategory_votes = defaultdict(list)
    left_right_votes = []
    axis_votes = defaultdict(list)
    axis_category_votes = defaultdict(lambda: defaultdict(list))

    vote_count = 0
    for vote_record in legislator_votes:
        bill_id = build_bill_id(vote_record.get("bill", {}))
        if bill_id == "":
            continue
        vote = vote_record.get("vote", "").strip()
        date = vote_record.get("date", "")

        if bill_id not in bill_analyses:
            continue

        bill_analysis = bill_analyses[bill_id]

        vote_value = get_vote_value(vote)
        if vote_value == 0:
            continue

        political_categories = bill_analysis.get("political_categories", {})

        # The caucus's average vote on this roll call. Unknown roll calls count as a vote
        # with the party, so they add nothing to the member's deviation from it.
        party_mean = None
        if party_relative:
            party_mean = party_means.get(vote_record.get("vote_id"), {}).get(
                caucus, vote_value
            )

        contest = None
        if contest_weights is not None:
            contest = contest_weights.get(vote_record.get("vote_id"))
            if contest is None and not _missing_contest_warned:
                _missing_contest_warned = True
                print(
                    f"WARNING: no contest weight for roll call {vote_record.get('vote_id')}; "
                    "votes without one are left out of the agreement index"
                )

        primary_scores = collect_category_votes(
            political_categories,
            bill_id,
            vote,
            date,
            vote_value,
            primary_category_votes,
            subcategory_votes,
            contest,
            party_mean,
        )
        if primary_scores:
            lr_score = sum(primary_scores) / len(primary_scores)
            lr_record = {
                "bill_id": bill_id,
                "weighted_score": lr_score,
                "contest": contest,
            }
            if party_mean is not None:
                # weighted_score = lean * vote_value, so lean = weighted_score * vote_value
                lr_record["base"] = lr_score * vote_value * party_mean
            left_right_votes.append(lr_record)

        if bill_id in axis_analyses:
            primary_names = [
                c["name"]
                for c in political_categories.get("primary_categories", [])
                if isinstance(c, dict) and c.get("name")
            ]
            collect_axis_votes(
                axis_analyses[bill_id],
                primary_names,
                bill_id,
                vote_value,
                axis_votes,
                axis_category_votes,
                contest,
            )

        vote_count += 1

    # Build primary category results with top_bills injected per category
    primary_category_results = calculate_average_scores(primary_category_votes)
    for category, votes in primary_category_votes.items():
        if category in primary_category_results:
            primary_category_results[category]["top_bills"] = extract_top_votes(votes)

    axis_results = build_axis_results(axis_votes, axis_category_votes)
    left_right = build_left_right_result(left_right_votes, primary_category_results)
    if left_right:
        axis_results[LEFT_RIGHT] = left_right

    # Flatten and deduplicate for overall recent votes
    all_votes_flat = [
        {**vote, "category": category}
        for category, votes in primary_category_votes.items()
        for vote in votes
    ]
    seen_bill_ids = set()
    all_votes_deduped = []
    for vote in sorted(all_votes_flat, key=lambda v: v.get("date", ""), reverse=True):
        if vote["bill_id"] not in seen_bill_ids:
            seen_bill_ids.add(vote["bill_id"])
            all_votes_deduped.append(vote)

    result = {
        "primary_category_classifications": primary_category_results,
        "subcategory_classifications": calculate_average_scores(subcategory_votes),
        "axis_classifications": axis_results,
        "vote_count": vote_count,
        "recent_votes": extract_recent_votes(all_votes_deduped),
    }

    if party_relative:
        result[PARTY_SUMS_KEY] = {
            "primary_categories": calculate_party_sums(primary_category_votes),
            "left_right": calculate_party_sums({LEFT_RIGHT: left_right_votes}).get(LEFT_RIGHT),
        }

    if contest_weights is not None:
        axis_sums = calculate_agreement_sums(axis_votes)
        if left_right_votes:
            axis_sums.update(
                calculate_agreement_sums({LEFT_RIGHT: left_right_votes})
            )
        result[AGREEMENT_SUMS_KEY] = {
            "primary_categories": calculate_agreement_sums(primary_category_votes),
            "axes": axis_sums,
            "axes_by_category": {
                axis: calculate_agreement_sums(by_category)
                for axis, by_category in axis_category_votes.items()
            },
        }

    return result


def create_legislator_profile(
    legislator_info,
    legislator_votes,
    bill_analyses,
    axis_analyses=None,
    contest_weights=None,
    party_means=None,
    caucus=None,
):
    """
    Create complete legislator ideology profile.

    Args:
        legislator_info (dict): Basic info {"member_id": "A000360", "name": "Rep. X", "party": "D", "state": "CA"}
        legislator_votes (list): Vote records
        bill_analyses (dict): Bill analysis results
        axis_analyses (dict): Bill axis analysis results, keyed by bill_id
        contest_weights (dict): vote_id -> contest weight; enables the agreement index

    Returns:
        dict: Complete legislator profile with ideology scores
    """
    ideology_data = calculate_legislator_ideology(
        legislator_votes,
        bill_analyses,
        axis_analyses,
        contest_weights,
        party_means,
        caucus,
    )

    profile = {
        "member_id": legislator_info.get("member_id"),
        "name": legislator_info.get("name"),
        "party": legislator_info.get("party"),
        "state": legislator_info.get("state"),
        "primary_categories": ideology_data["primary_category_classifications"],
        "axes": ideology_data["axis_classifications"],
        "vote_count": ideology_data["vote_count"],
        "recent_votes": ideology_data["recent_votes"],
    }
    # Kept at the top level, out of the stats dicts, until apply_agreement and
    # apply_party_relative consume them
    for key in (AGREEMENT_SUMS_KEY, PARTY_SUMS_KEY):
        if key in ideology_data:
            profile[key] = ideology_data[key]

    return profile


def build_legislator_info(legislator_data):
    legislator_info = {
        "member_id": legislator_data.get("member_id"),
        "name": legislator_data.get("name"),
        "party": legislator_data.get("party"),
        "state": legislator_data.get("state"),
    }
    return legislator_info


def load_caucus_parties():
    """member_id -> party their latest term caucuses with (independents get their caucus)."""
    caucuses = {}
    for leg in db_utils.get_collection("legislators").find(
        {"member_id": {"$exists": True}}, {"member_id": 1, "terms": {"$slice": -1}}
    ):
        if leg.get("terms"):
            caucuses[leg["member_id"]] = caucus_party(leg["terms"][-1])
    return caucuses


def profile_caucus(party, member_id, caucus_parties):
    """
    The party a member is compared with: their own if D or R, else the one they caucus with
    (Sanders and King caucus with Democrats). None if neither is D or R.
    """
    if party in PARTIES:
        return party
    caucus = caucus_parties.get(member_id)
    return caucus if caucus in PARTIES else None


def caucus_of(profile):
    """Comparison party of a profile; falls back to `party` for profiles built without one."""
    return profile.get("caucus", profile.get("party"))


def build_institutional_stats(institutional_scores, member_id):
    """Axis stats for the computed institutional axis, or None if the member has none."""
    stats = institutional_scores.get(member_id)
    if stats is None:
        return None
    return {
        "score": stats["score"],
        # Roll calls rather than bills, but named bill_count so every axis has the same shape
        "bill_count": stats["vote_count"],
        "defection_rate": stats["defection_rate"],
        "by_category": {},
    }


def gather_legislator_votes(
    member_id, extra_votes_by_member=None, include_db_votes=True, chamber=None
):
    """
    A legislator's vote records: member_votes docs, plus any extra in-memory records.
    With `chamber`, only roll calls in that chamber: a senator who served in the House keeps
    one member_id for both, so their House votes would otherwise count toward the Senate.
    """
    votes = (
        list(db_utils.get_collection("member_votes").find({"member_id": member_id}))
        if include_db_votes
        else []
    )
    if extra_votes_by_member:
        votes += extra_votes_by_member.get(member_id, [])
    if chamber:
        code = CHAMBER_CODES[chamber]
        votes = [v for v in votes if str(v.get("vote_id", "")).startswith(code)]
    return votes


def process_all_legislators(
    bill_analyses,
    model,
    spec_hash,
    schema=None,
    chamber=None,
    axis_analyses=None,
    axis_schema=None,
    institutional_scores=None,
    contest_weights=None,
    extra_votes_by_member=None,
    include_db_votes=True,
    party_means=None,
):
    """
    Process ideology scores for all legislators.

    extra_votes_by_member ({member_id: [vote records]}) adds in-memory votes (amendment
    votes from calc_amendment_ideology.py); include_db_votes=False leaves out member_votes.
    The defaults give exactly the member_votes records, as before.
    contest_weights (vote_id -> contest weight) enables the agreement index.
    party_means (from load_party_vote_means) enables party-relative scores; run
    apply_party_relative on the result before Bayesian weighting.
    """

    # If schema not specified, use latest version
    if schema is None:
        schema = SCHEMA_VERSION
    if axis_schema is None:
        axis_schema = AXIS_SCHEMA_VERSION
    institutional_scores = institutional_scores or {}

    profiles = []
    caucus_parties = load_caucus_parties()
    # Get collection
    members_with_votes_col = db_utils.get_collection("members_with_votes")

    for legislator_data in members_with_votes_col.find():
        # Senator's member_id looks like SXXX (Ex: S313), House uses bioguide which has 7 chars
        if chamber == "house":
            if len(legislator_data["member_id"]) <= 4:
                continue
        elif chamber == "senate":
            if len(legislator_data["member_id"]) > 4:
                continue
        legislator_info = build_legislator_info(legislator_data)
        caucus = profile_caucus(
            legislator_info["party"], legislator_info["member_id"], caucus_parties
        )
        # get legislator votes from member_votes collection
        legislator_votes = gather_legislator_votes(
            legislator_data["member_id"], extra_votes_by_member, include_db_votes, chamber
        )

        profile = create_legislator_profile(
            legislator_info,
            legislator_votes,
            bill_analyses,
            axis_analyses,
            contest_weights,
            party_means,
            caucus,
        )

        # Don't add profiles for legislator's that had no votes with the active filters
        if profile["vote_count"] == 0:
            continue
        profile["caucus"] = caucus

        institutional = build_institutional_stats(
            institutional_scores, legislator_data["member_id"]
        )
        if institutional:
            profile["axes"][INSTITUTIONAL] = institutional

        # Add unique metadata
        profile["model"] = model
        profile["schema_version"] = schema
        profile["axis_schema_version"] = axis_schema
        profile["spec_hash"] = spec_hash

        profiles.append(profile)

    return profiles


def load_bill_analyses_from_data():
    """Load a dictionary of all bill analyses from data/ directory"""
    bill_analyses = {}

    for congress in DATA_DIR.iterdir():
        if not congress.is_dir():
            continue

        bills_dir = congress / "bills"
        if not bills_dir.exists():
            print(f"WARNING: No bills folder in {congress}")
            continue

        # Iterate through each bill type folder
        for bill_type_folder in bills_dir.iterdir():
            if not bill_type_folder.is_dir():
                continue

            # Ignore hres, sres, hconres, sconres
            # These are simple and concurrent resolutions that have no lawful power
            if bill_type_folder.name not in {"hr", "hjres", "s", "sjres"}:
                continue

            # Iterate through all individual bill folders
            for folder in bill_type_folder.iterdir():
                if not folder.is_dir():
                    continue

                # Check if bill was analyzed
                analysis_file = folder / "bill_analysis.json"
                if not analysis_file.exists():
                    continue

                with open(analysis_file, "r") as f:
                    analysis_data = json.load(f)

                # Congress + folder = id, ex. 119 + hr26 = hr25-119
                bill_id = folder.name + "-" + congress.name

                bill_analyses[bill_id] = analysis_data

    return bill_analyses


def get_spec_hash(
    model, schema, congress=None, chamber=None, bill_type=None, axis_schema=None
) -> str:
    """Generate a readable spec hash like: model_schema_axN_congress_chamber_billtype.

    Model and schema are required. Axis schema, congress, chamber, and bill_type are optional.
    """
    # If not specified, latest was used
    if schema is None:
        schema = SCHEMA_VERSION
    if axis_schema is None:
        axis_schema = AXIS_SCHEMA_VERSION

    parts = [model, str(schema), f"ax{axis_schema}"]
    if congress:
        parts.append(str(congress))
    else:
        parts.append("all")
    if chamber:
        parts.append(chamber.lower())
    else:
        parts.append("all")
    if bill_type:
        # sort for consistency (variance in inputting different bill_types)
        parts.append(",".join(sorted(bt.lower() for bt in bill_type)))
    else:
        parts.append("all")

    return "_".join(parts)


def load_analyses_from_db(collection_name, query, congress=None, bill_type=None):
    """Load analyses matching `query` from a collection, keyed by bill_id."""
    # Optional filters
    if congress:
        query["congress"] = str(congress)
    if bill_type:
        query["bill_type"] = {"$in": bill_type}

    analyses = {}
    for analysis_data in db_utils.get_collection(collection_name).find(query):
        bill_id = analysis_data.get("bill_id")
        if bill_id and bill_id not in analyses:
            analyses[bill_id] = analysis_data
    return analyses


def load_bill_analyses_from_db(
    model, schema_version=None, congress=None, bill_type=None
):
    """
    Load bill analyses from MongoDB, filtering by model and optionally schema version.
    """
    if schema_version is None:
        # Get latest schema version available
        schema_version = SCHEMA_VERSION

    query = {"model": model, "schema_version": schema_version}
    bill_analyses = load_analyses_from_db(INPUT_COLLECTION, query, congress, bill_type)

    print(
        f"Loaded {len(bill_analyses)} bill analyses for model '{model}' (schema v{schema_version})"
    )
    return bill_analyses


def load_axis_analyses_from_db(
    model, axis_schema_version=None, congress=None, bill_type=None
):
    """
    Load bill axis analyses from MongoDB, filtering by model and optionally axis schema version.
    """
    if axis_schema_version is None:
        axis_schema_version = AXIS_SCHEMA_VERSION

    query = {"model": model, "axis_schema_version": axis_schema_version}
    axis_analyses = load_analyses_from_db(
        AXIS_INPUT_COLLECTION, query, congress, bill_type
    )

    print(
        f"Loaded {len(axis_analyses)} axis analyses for model '{model}' (axis schema v{axis_schema_version})"
    )
    return axis_analyses


def apply_party_relative(profiles):
    """
    Replace each primary-category score and the left_right score with a party-relative one:

        score = caucus baseline + member's average deviation from their caucus

    A member's raw average mixes two things: how they voted, and which bills they happened
    to vote on (which congresses they served in). The deviation term keeps only the first:
    each vote counts weighted_score minus what the caucus's average vote on that same roll
    call would have scored. The baseline is the caucus's vote-weighted average of the
    party-line part, so the gap between parties stays where it was. Members with no D/R
    caucus keep their raw scores. left_right's by_category copies the new category scores.
    """
    totals = defaultdict(lambda: {"base": 0.0, "n": 0})

    def items(sums):
        for name, item in (sums.get("primary_categories") or {}).items():
            yield ("primary_categories", name), item
        if sums.get("left_right"):
            yield ("axes", LEFT_RIGHT), sums["left_right"]

    for profile in profiles:
        sums = profile.get(PARTY_SUMS_KEY)
        if not sums:
            continue
        for key, item in items(sums):
            total = totals[(caucus_of(profile), key)]
            total["base"] += item["base"]
            total["n"] += item["n"]

    for profile in profiles:
        sums = profile.pop(PARTY_SUMS_KEY, None)
        if not sums:
            continue
        caucus = caucus_of(profile)
        for (field, name), item in items(sums):
            stats = profile.get(field, {}).get(name)
            total = totals[(caucus, (field, name))]
            if stats is None or not item["n"] or not total["n"]:
                continue
            baseline = total["base"] / total["n"]
            deviation = (item["ws"] - item["base"]) / item["n"]
            stats["score"] = round(baseline + deviation, 3)

        left_right = profile.get("axes", {}).get(LEFT_RIGHT)
        if left_right:
            for category, category_stats in left_right.get("by_category", {}).items():
                if category in profile.get("primary_categories", {}):
                    category_stats["score"] = profile["primary_categories"][category]["score"]

    return profiles


def apply_bayesian_weighting(profiles, field="primary_categories", skip=()):
    """
    Applies Bayesian weighting to legislator scores using their caucus's average as the prior
    (independents use the party they caucus with).
    m is determined empirically per category based on the distribution of bill counts.
    `field` is the profile dict of {name: stats} to weight; names in `skip` are left as is.
    """
    # Collect all counts and raw data for prior calculation
    category_counts = defaultdict(list)
    party_totals = defaultdict(lambda: defaultdict(lambda: {"sum": 0, "count": 0}))

    # We use a threshold to ensure our "Party Average" (the Prior) is built from
    # legislators who actually have enough data to be representative
    MINIMUM_VOTE_FOR_PRIOR = 10

    for profile in profiles:
        party = caucus_of(profile)
        categories = profile.get(field, {})

        for cat_name, cat_data in categories.items():
            if cat_name in skip:
                continue
            count = cat_data.get("bill_count", 0)
            if count > 0:
                category_counts[cat_name].append(count)

            # Build party totals for the prior (C)
            if party in ["D", "R"] and count > MINIMUM_VOTE_FOR_PRIOR:
                party_totals[party][cat_name]["sum"] += cat_data["score"] * count
                party_totals[party][cat_name]["count"] += count

    # Compute the empirical 'm' per category (25th percentile)
    m_per_category = {}
    for cat_name, counts in category_counts.items():
        if counts:
            # We use the 25th percentile. If a category is sparse, m will be low.
            # If a category is dense, m will be higher to ensure stability.
            m_per_category[cat_name] = max(5, pd.Series(counts).quantile(0.25))
        else:
            m_per_category[cat_name] = 10  # Fallback

    # 3. Compute the party means (C)
    party_means = {}
    for party, cats in party_totals.items():
        party_means[party] = {}
        for cat_name, stats in cats.items():
            if stats["count"] > 0:
                party_means[party][cat_name] = stats["sum"] / stats["count"]
            else:
                party_means[party][cat_name] = 0

    # 4. Apply the Bayesian Formula
    for profile in profiles:
        party = caucus_of(profile)

        for cat_name, cat_data in profile.get(field, {}).items():
            if cat_name in skip:
                continue
            v = cat_data.get("bill_count", 0)
            R = cat_data.get("score", 0)
            m = m_per_category.get(cat_name, 10)

            # Get the prior (C)
            if party in party_means and cat_name in party_means[party]:
                C = party_means[party][cat_name]
            else:
                # Fallback to absolute center (0) for members with no D/R caucus
                C = 0

            # Bayesian Calculation: W = (v*R + m*C) / (v + m)
            weighted_score = (v / (v + m)) * R + (m / (v + m)) * C

            # Store data
            cat_data["raw_score"] = R
            cat_data["score"] = round(weighted_score, 3)
            cat_data["party_prior"] = round(C, 3)
            cat_data["empirical_m"] = round(m, 1)

    return profiles


def add_within_party_z(profiles):
    """
    Add `within_party_z` to every axis: the score's z-score among members of the same caucus
    (D or R; independents count with the party they caucus with) with enough bills on that
    axis. None where it can't be computed.
    """
    groups = defaultdict(list)
    for profile in profiles:
        party = caucus_of(profile)
        for axis, stats in profile.get("axes", {}).items():
            stats["within_party_z"] = None
            if party in PARTIES and stats["bill_count"] >= MIN_BILLS_FOR_Z:
                groups[(party, axis)].append(stats)

    for group in groups.values():
        z_scores = within_group_zscores([stats["score"] for stats in group])
        for stats, z in zip(group, z_scores):
            stats["within_party_z"] = None if z is None else round(z, 3)

    return profiles


def generate_rankings(profiles):
    """Generate rankings for each category and axis across all profiles."""
    all_rows = []
    all_current_rows = []

    # Get current legislators
    current_legislators = set()
    legislator_collection = db_utils.get_collection("legislators").find(
        {"current": True}
    )
    for legislator in legislator_collection:
        # Check latest term to determine if senator or house member
        if legislator["terms"][-1]["type"] == "sen":
            current_legislators.add(legislator.get("lis"))
        else:
            current_legislators.add(legislator.get("bioguide"))

    # Collect data from every profile
    for profile in profiles:
        is_current = profile["member_id"] in current_legislators
        for field in RANKED_FIELDS:
            data = profile.get(field, {})

            for category, details in data.items():

                all_rows.append(
                    {
                        "field": field,
                        "category": category,
                        "score": details["score"],
                        "party": profile.get("party"),
                        "member_id": profile.get("member_id"),
                        "name": profile.get("name"),
                    }
                )
                if is_current:
                    all_current_rows.append(
                        {
                            "field": field,
                            "category": category,
                            "score": details["score"],
                            "party": profile.get("party"),
                            "member_id": profile.get("member_id"),
                            "name": profile.get("name"),
                        }
                    )

    # No rankings generated
    if all_rows == [] or all_current_rows == []:
        print("Cant generate rankings, not enough data yet")
        return profiles

    # Create one big DataFrame
    df = pd.DataFrame(all_rows)
    current_df = pd.DataFrame(all_current_rows)

    # Find counts
    total_counts = df.groupby(["field", "category"]).size().rename("total_members")

    total_current_counts = (
        current_df.groupby(["field", "category"]).size().rename("total_current_members")
    )

    # Compute rankings *within each (field, category) group*
    df["rank"] = df.groupby(["field", "category"])["score"].rank(
        method="min", ascending=False
    )
    df["percentile_rank"] = df.groupby(["field", "category"])["score"].rank(
        pct=True, ascending=True
    )
    current_df["current_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(method="min", ascending=False)
    current_df["current_percentile_rank"] = current_df.groupby(["field", "category"])[
        "score"
    ].rank(pct=True, ascending=True)

    # Now push results back into each profile
    for profile in profiles:
        is_current = profile["member_id"] in current_legislators
        for field in RANKED_FIELDS:
            sub = df[(df["field"] == field) & (df["member_id"] == profile["member_id"])]

            for _, row in sub.iterrows():
                cat = row["category"]
                # make sure the category exists
                if cat not in profile[field]:
                    profile[field][cat] = {}

                total_members = int(total_counts.get((field, cat), 0))
                total_current_members = int(total_current_counts.get((field, cat), 0))

                profile[field][cat].update(
                    {
                        "rank": int(row["rank"]),
                        "percentile_rank": round(row["percentile_rank"], 3),
                        # Add member count info (so we can contextualize rankings)
                        "total_members": total_members,
                        "total_current_members": total_current_members,
                    }
                )
                # Set to -1 (N/A), will be updated in following loop if current
                profile[field][cat].update(
                    {
                        "current_rank": -1,
                        "current_percentile_rank": -1,
                    }
                )

            # Have to iterate through current ranks separately (since its a different dataframe)
            if is_current:
                current_sub = current_df[
                    (current_df["field"] == field)
                    & (current_df["member_id"] == profile["member_id"])
                ]
                for _, current_row in current_sub.iterrows():
                    cat = current_row["category"]

                    profile[field][cat].update(
                        {
                            "current_rank": int(current_row["current_rank"]),
                            "current_percentile_rank": round(
                                current_row["current_percentile_rank"], 3
                            ),
                        }
                    )

    return profiles


def iter_agreement_sums(profile):
    """(key, sums) for each scored item of a profile; key is (field, name[, category])."""
    sums = profile.get(AGREEMENT_SUMS_KEY, {})
    for name, item in sums.get("primary_categories", {}).items():
        yield ("primary_categories", name), item
    for name, item in sums.get("axes", {}).items():
        yield ("axes", name), item
    for axis, by_category in sums.get("axes_by_category", {}).items():
        for category, item in by_category.items():
            yield ("axes_by_category", axis, category), item


def apply_agreement(profiles):
    """
    Add an `agreement` block (the contested-vote agreement index) next to every existing
    score, then drop the temporary sums. k is set per (field, name) across all profiles,
    i.e. per run. Only stats dicts that already exist get a block; institutional gets none.
    """
    pooled = defaultdict(list)
    for profile in profiles:
        for key, item in iter_agreement_sums(profile):
            pooled[key].extend(item["weights"])
    shrink_k = {key: compute_shrink_k(weights) for key, weights in pooled.items()}

    def block(profile, key):
        item = profile[AGREEMENT_SUMS_KEY].get(key[0], {})
        for part in key[1:]:
            item = item.get(part, {}) if isinstance(item, dict) else {}
        item = item if "num" in item else {"num": 0.0, "den": 0, "vote_count": 0}
        stats = finalize(
            item["num"], item["den"], item["vote_count"], shrink_k.get(key)
        )
        stats["within_party_z"] = None
        return stats

    groups = defaultdict(list)
    for profile in profiles:
        if AGREEMENT_SUMS_KEY not in profile:
            continue
        party = caucus_of(profile)

        for name, stats in profile.get("primary_categories", {}).items():
            stats["agreement"] = block(profile, ("primary_categories", name))
            groups[(party, "primary_categories", name)].append(stats["agreement"])

        for axis, stats in profile.get("axes", {}).items():
            if axis == INSTITUTIONAL:
                continue
            stats["agreement"] = block(profile, ("axes", axis))
            groups[(party, "axes", axis)].append(stats["agreement"])

            for category, category_stats in stats.get("by_category", {}).items():
                if axis == LEFT_RIGHT:
                    # left_right's breakdown is the category scores themselves
                    source = block(profile, ("primary_categories", category))
                else:
                    source = block(profile, ("axes_by_category", axis, category))
                source.pop("within_party_z")
                category_stats["agreement"] = source

    # Within-party z of the index, same eligibility rule as add_within_party_z
    for (party, _, _), group in groups.items():
        eligible = [
            a
            for a in group
            if party in PARTIES
            and a["vote_count"] >= MIN_BILLS_FOR_Z
            and a["score"] is not None
        ]
        z_scores = within_group_zscores([a["score"] for a in eligible])
        for a, z in zip(eligible, z_scores):
            a["within_party_z"] = None if z is None else round(z, 3)

    for profile in profiles:
        profile.pop(AGREEMENT_SUMS_KEY, None)

    return profiles


def write_profiles_to_db(profiles):
    """Write legislator profiles to MongoDB collection."""
    count = 0
    actions = []
    for profile in profiles:
        query = {
            "member_id": profile["member_id"],
            "model": profile["model"],
            "schema_version": profile["schema_version"],
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
        db_utils.bulk_write(OUTPUT_COLLECTION, actions)
    print(f"Updated profile for {count} members")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # REQUIRED ARGUMENT
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
    parser.add_argument(
        "--axis_schema",
        type=int,
        help="Optionally specify axis schema version (defaults to latest)",
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

    # Load axis analyses for the same model (axes are skipped if none exist yet)
    axis_analyses = load_axis_analyses_from_db(
        args.model, args.axis_schema, args.congress, args.bill_type
    )
    if not axis_analyses:
        print("WARNING: No axis analyses found, profiles will only have category scores")

    # Check if bill analyses is empty
    if bill_analyses:

        # Get hash
        spec_hash = get_spec_hash(
            args.model,
            args.schema,
            args.congress,
            args.chamber,
            args.bill_type,
            args.axis_schema,
        )

        # Institutional axis is computed from all roll calls, not just analyzed bills
        institutional_scores = calculate_institutional_scores(
            args.congress, args.chamber
        )

        # Contest weight of every passage roll call (for the agreement index)
        contest_weights = load_contest_weights(args.congress, args.chamber)
        # Each caucus's average vote on every passage roll call (for party-relative scores)
        party_means = load_party_vote_means(args.congress, args.chamber)

        # Process all legislators
        profiles = process_all_legislators(
            bill_analyses,
            args.model,
            spec_hash,
            args.schema,
            args.chamber,
            axis_analyses,
            args.axis_schema,
            institutional_scores,
            contest_weights,
            party_means=party_means,
        )

        # Compare each member with their own caucus on the same roll calls
        profiles = apply_party_relative(profiles)

        # Apply Bayesian weighting (institutional is already computed over all roll calls)
        profiles = apply_bayesian_weighting(profiles)
        profiles = apply_bayesian_weighting(profiles, "axes", skip={INSTITUTIONAL})

        # Compare members to their own party
        profiles = add_within_party_z(profiles)

        # Create rankings for each category and axis
        profiles = generate_rankings(profiles)

        # Contested-vote agreement index, added next to the existing scores
        profiles = apply_agreement(profiles)

        # Write profiles
        write_profiles_to_db(profiles)
    else:
        print(
            f"No analyses were found for model: {args.model} with schema version: {args.schema if args.schema else 'latest'}"
        )
