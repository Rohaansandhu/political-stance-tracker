import pytest

import calc_member_ideology as cmi


def analysis(partisan):
    return {
        "political_categories": {
            "primary_categories": [
                {"name": "Healthcare", "partisan_score": partisan, "impact_score": 1.0}
            ]
        }
    }


BILLS = {
    # A bipartisan liberal-leaning bill (older congress) and a conservative bill (newer one)
    "hr1-118": analysis(-0.2),
    "hr2-119": analysis(0.8),
    "hr3-119": analysis(0.5),
}
MEANS = {
    "h1-118.2023": {"D": 1.0, "R": 0.6},
    "h2-119.2025": {"D": -1.0, "R": 1.0},
    "h3-119.2025": {"D": 0.0, "R": 1.0},
}


def vote(vote_id, bill, choice):
    number, congress = bill[2:].split("-")
    return {"vote_id": vote_id, "bill": {"type": "hr", "number": number, "congress": congress}, "vote": choice}


def profile(member_id, votes, caucus="D"):
    p = cmi.create_legislator_profile(
        {"member_id": member_id, "name": member_id, "party": caucus, "state": "XX"},
        votes,
        BILLS,
        party_means=MEANS,
        caucus=caucus,
    )
    p["caucus"] = caucus
    return p


def test_party_vote_means_counts_independents_with_caucus():
    rollcall = {
        "votes": {
            "Yea": [{"id": "A", "party": "D"}, {"id": "B", "party": "I"}, {"id": "C", "party": "R"}],
            "Nay": [{"id": "D", "party": "R"}, "VP"],
            "Not Voting": [{"id": "E", "party": "D"}],
        }
    }
    caucus = {"B": "D"}
    means = cmi.party_vote_means(rollcall, lambda v: v["party"] if v["party"] in "DR" else caucus.get(v["id"]))
    assert means == {"D": 1.0, "R": 0.0}


def test_loyal_members_score_the_same_whatever_they_served_through():
    # Both vote exactly with Democrats' majority, on different congresses' bills
    old = profile("OLD", [vote("h1-118.2023", "hr1-118", "Yea")])
    new = profile("NEW", [vote("h2-119.2025", "hr2-119", "Nay")])
    raw_old = old["primary_categories"]["Healthcare"]["score"]
    raw_new = new["primary_categories"]["Healthcare"]["score"]
    assert raw_old != raw_new  # the raw averages differ only because of the vote mix

    cmi.apply_party_relative([old, new])
    assert old["primary_categories"]["Healthcare"]["score"] == new["primary_categories"]["Healthcare"]["score"]
    assert old["axes"]["left_right"]["score"] == new["axes"]["left_right"]["score"]
    assert cmi.PARTY_SUMS_KEY not in old


def test_breaking_toward_the_other_party_moves_the_score_that_way():
    loyal = profile("LOYAL", [vote("h3-119.2025", "hr3-119", "Nay"), vote("h2-119.2025", "hr2-119", "Nay")])
    rebel = profile("REBEL", [vote("h3-119.2025", "hr3-119", "Yea"), vote("h2-119.2025", "hr2-119", "Nay")])
    cmi.apply_party_relative([loyal, rebel])
    assert rebel["axes"]["left_right"]["score"] > loyal["axes"]["left_right"]["score"]
    # left_right's per-category breakdown follows the adjusted category score
    lr = rebel["axes"]["left_right"]["by_category"]["Healthcare"]["score"]
    assert lr == rebel["primary_categories"]["Healthcare"]["score"]


def test_without_party_means_scores_are_unchanged():
    p = cmi.create_legislator_profile(
        {"member_id": "X", "name": "X", "party": "D", "state": "XX"},
        [vote("h1-118.2023", "hr1-118", "Yea")],
        BILLS,
    )
    assert cmi.PARTY_SUMS_KEY not in p
    assert p["primary_categories"]["Healthcare"]["score"] == pytest.approx(-0.2)


def test_members_without_a_caucus_keep_raw_scores():
    p = profile("IND", [vote("h1-118.2023", "hr1-118", "Yea")], caucus=None)
    assert cmi.PARTY_SUMS_KEY not in p
    cmi.apply_party_relative([p])
    assert p["primary_categories"]["Healthcare"]["score"] == pytest.approx(-0.2)
