"""
Computed `institutional` axis: how often a member votes against their party's leadership.

Score runs from -1 (Establishment, always votes with leadership) to +1 (Insurgent, always
defects). Unlike the LLM-scored axes this uses every roll call, not just analyzed bills.
"""

import argparse
import re
from collections import Counter, defaultdict

import db.db_utils as db_utils
from process_votes_by_member import get_legislator_id_map
from utils.vote_utils import get_vote_value

PARTIES = {"D", "R"}

# Only members holding these roles count as "party leadership" (floor leaders and whips)
LEADER_TITLES = {
    "Speaker of the House",
    "House Majority Leader",
    "House Majority Whip",
    "House Minority Leader",
    "House Minority Whip",
    "Senate Majority Leader",
    "Senate Majority Whip",
    "Senate Minority Leader",
    "Senate Minority Whip",
}

CHAMBER_NAMES = {"h": "house", "s": "senate"}
CAUCUS_PARTIES = {"Democrat": "D", "Republican": "R"}

# Weight of a vote by the roll call `category` in the congress vote data
DEFAULT_VOTE_WEIGHT = 1.0
VOTE_WEIGHTS = {
    "leadership": 3.0,  # speaker elections
    "procedural": 2.0,  # rules, previous question, motions to proceed
    "cloture": 1.5,
    "veto-override": 1.5,
    "passage": 1.0,
    "recommit": 1.0,
    "nomination": 0.5,
    "amendment": 0.5,
    "passage-suspension": 0.25,
}

# Must-pass passage votes count extra
MUST_PASS_PATTERN = re.compile(
    r"appropriations|continuing resolution|debt (limit|ceiling)|"
    r"national defense authorization|government funding",
    re.IGNORECASE,
)
MUST_PASS_MULTIPLIER = 2.0

# Votes where the losing side is below this share of voters barely test loyalty
NEAR_UNANIMOUS_LOSING_SHARE = 0.10
NEAR_UNANIMOUS_MULTIPLIER = 0.25

# Non-choices: don't count toward a member's loyalty
NON_VOTES = {"not voting", "present"}


def normalize_option(vote_key):
    """
    Map a roll call option to a comparable choice. Yea/Aye/Nay/No collapse to yea/nay;
    anything else (e.g. a candidate in a speaker election) is its own option.
    Returns None for non-votes.
    """
    key = vote_key.strip().lower()
    if key in NON_VOTES:
        return None

    value = get_vote_value(key)
    if value == 1:
        return "yea"
    if value == -1:
        return "nay"
    return key


def caucus_party(term):
    """Party a term caucuses with. Independents use their `caucus` field if they have one."""
    return CAUCUS_PARTIES.get(term.get("caucus"), term.get("party", "")[:1])


class Roster:
    """Who sat in which party and held which leadership role on a given date."""

    def __init__(self, legislators):
        self.terms = defaultdict(list)
        self.leaders = []

        for leg in legislators:
            member_id = leg["member_id"]
            for term in leg.get("terms", []):
                self.terms[member_id].append(
                    (term["start"], term.get("end", "9999-12-31"), caucus_party(term))
                )
            for role in leg.get("leadership_roles", []):
                if role["title"] in LEADER_TITLES:
                    self.leaders.append(
                        (
                            member_id,
                            role["chamber"],
                            role["start"],
                            role.get("end", "9999-12-31"),
                        )
                    )

    def party_on(self, member_id, date):
        """Caucus party of a member on `date` (YYYY-MM-DD), or None if unknown."""
        for start, end, party in self.terms.get(member_id, []):
            if start <= date <= end:
                return party
        return None

    def leaders_on(self, chamber, date):
        """Member ids of party leaders in `chamber` ('house'/'senate') on `date`."""
        return [
            member_id
            for member_id, role_chamber, start, end in self.leaders
            if role_chamber == chamber and start <= date <= end
        ]


def load_roster():
    # Only members who could have voted in the data (113th Congress began 2013)
    legislators = db_utils.get_collection("legislators").find(
        {"terms.end": {"$gte": "2013-01-01"}},
        {"member_id": 1, "terms": 1, "leadership_roles": 1},
    )
    return Roster(legislators)


def collect_choices(vote, id_map, roster, date):
    """Map each member who cast a vote to (caucus party, normalized option)."""
    choices = {}
    for vote_key, voters in vote.get("votes", {}).items():
        option = normalize_option(vote_key)
        if option is None:
            continue
        for voter in voters:
            if not isinstance(voter, dict) or not voter.get("id"):
                continue
            member_id = id_map.get(voter["id"], voter["id"])
            party = roster.party_on(member_id, date) or voter.get("party")
            choices[member_id] = (party, option)
    return choices


def party_positions(choices, leader_ids):
    """
    Each party's leadership position on a roll call: the majority option among its leaders
    who voted, falling back to the plurality of the party's members.
    """
    positions = {}
    for party in PARTIES:
        leader_options = Counter(
            choices[m][1] for m in leader_ids if m in choices and choices[m][0] == party
        )
        member_options = Counter(o for p, o in choices.values() if p == party)

        for options in (leader_options, member_options):
            ranked = options.most_common(2)
            # Skip empty counts and exact ties (no clear position)
            if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
                positions[party] = ranked[0][0]
                break
    return positions


def vote_weight(vote, choices):
    """Weight of a roll call for loyalty: by type, must-pass status, and how contested it was."""
    weight = VOTE_WEIGHTS.get(vote.get("category"), DEFAULT_VOTE_WEIGHT)

    text = f"{vote.get('subject', '')} {vote.get('question', '')}"
    if vote.get("category") == "passage" and MUST_PASS_PATTERN.search(text):
        weight *= MUST_PASS_MULTIPLIER

    option_counts = Counter(option for _, option in choices.values())
    losing_share = 1 - max(option_counts.values()) / sum(option_counts.values())
    if losing_share < NEAR_UNANIMOUS_LOSING_SHARE:
        weight *= NEAR_UNANIMOUS_MULTIPLIER

    return weight


def calculate_institutional_scores(congress=None, chamber=None):
    """
    Compute the institutional score for every member over all matching roll calls.

    Args:
        congress (int): Only include roll calls from this congress
        chamber (str): "house" or "senate"

    Returns:
        dict: member_id -> {"score", "defection_rate", "vote_count"}
    """
    query = {}
    if congress:
        query["congress"] = congress
    if chamber:
        query["chamber"] = {"house": "h", "senate": "s"}[chamber]

    id_map = get_legislator_id_map()
    roster = load_roster()

    totals = defaultdict(lambda: {"weight": 0.0, "defected": 0.0, "votes": 0})

    for vote in db_utils.get_collection("rollcall_votes").find(query):
        date = vote["date"][:10]
        chamber_name = CHAMBER_NAMES[vote["chamber"]]

        choices = collect_choices(vote, id_map, roster, date)
        if not choices:
            continue

        positions = party_positions(
            choices, roster.leaders_on(chamber_name, date)
        )
        weight = vote_weight(vote, choices)

        for member_id, (party, option) in choices.items():
            if party not in positions:
                continue
            totals[member_id]["weight"] += weight
            totals[member_id]["votes"] += 1
            if option != positions[party]:
                totals[member_id]["defected"] += weight

    scores = {}
    for member_id, t in totals.items():
        rate = t["defected"] / t["weight"]
        scores[member_id] = {
            # -1 = never defects (Establishment), +1 = always defects (Insurgent)
            "score": round(2 * rate - 1, 3),
            "defection_rate": round(rate, 4),
            "vote_count": t["votes"],
        }
    return scores


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print the most and least loyal members, as a sanity check"
    )
    parser.add_argument("--congress", type=int, help="Only include this congress")
    parser.add_argument("--chamber", choices=["house", "senate"])
    parser.add_argument("--top", type=int, default=10, help="Members to show per end")
    parser.add_argument(
        "--min_votes", type=int, default=50, help="Skip members with fewer votes"
    )
    args = parser.parse_args()

    scores = calculate_institutional_scores(args.congress, args.chamber)
    names = {
        m["member_id"]: m.get("name")
        for m in db_utils.get_collection("members_with_votes").find()
    }

    ranked = sorted(
        (
            (member_id, s)
            for member_id, s in scores.items()
            if s["vote_count"] >= args.min_votes
        ),
        key=lambda item: item[1]["defection_rate"],
    )

    def show(label, rows):
        print(f"\n{label}")
        for member_id, s in rows:
            print(
                f"  {names.get(member_id, member_id):<24} rate={s['defection_rate']:.3f} "
                f"score={s['score']:+.3f} votes={s['vote_count']}"
            )

    show("Most loyal (Establishment)", ranked[: args.top])
    show("Most defecting (Insurgent)", ranked[::-1][: args.top])
