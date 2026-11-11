import calc_member_ideology as cmi


def test_profile_caucus_uses_caucus_for_independents():
    caucuses = {"S313": "D", "S999": "I"}
    assert cmi.profile_caucus("D", "X", caucuses) == "D"
    assert cmi.profile_caucus("R", "S313", caucuses) == "R"
    assert cmi.profile_caucus("I", "S313", caucuses) == "D"
    assert cmi.profile_caucus("I", "S999", caucuses) is None
    assert cmi.profile_caucus("I", "missing", caucuses) is None


def test_caucus_of_falls_back_to_party():
    assert cmi.caucus_of({"party": "I", "caucus": "D"}) == "D"
    assert cmi.caucus_of({"party": "R"}) == "R"
    assert cmi.caucus_of({"party": "I", "caucus": None}) is None


def test_independent_gets_caucus_prior_not_zero():
    def member(mid, party, caucus, score):
        return {
            "member_id": mid,
            "party": party,
            "caucus": caucus,
            "axes": {"left_right": {"score": score, "bill_count": 100}},
        }

    profiles = [
        member("D1", "D", "D", -0.2),
        member("D2", "D", "D", -0.2),
        member("R1", "R", "R", 0.2),
        member("I1", "I", "D", -0.25),
    ]
    cmi.apply_bayesian_weighting(profiles, "axes")
    independent = profiles[3]["axes"]["left_right"]
    assert independent["party_prior"] < -0.19
    assert independent["score"] < -0.2


def test_gather_votes_keeps_only_the_chamber(monkeypatch):
    extra = {"S1": [{"vote_id": "h10-115.2017"}, {"vote_id": "s5-117.2021"}]}
    votes = cmi.gather_legislator_votes("S1", extra, include_db_votes=False, chamber="senate")
    assert [v["vote_id"] for v in votes] == ["s5-117.2021"]
    votes = cmi.gather_legislator_votes("S1", extra, include_db_votes=False, chamber=None)
    assert len(votes) == 2
