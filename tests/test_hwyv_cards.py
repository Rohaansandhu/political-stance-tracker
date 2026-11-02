import json

import create_hwyv_cards as hwyv
from schema.hwyv_cards import BRIEF_FIELDS, HwyvCard, brief_fields
from utils.agreement_utils import contest_weight

AXES = ["fiscal", "trade", "social"]
CATEGORIES = ["Healthcare", "Immigration"]
REVEAL_KEYS = {
    "result",
    "requires",
    "tally",
    "sponsor",
    "sponsor_party",
    "source_url",
    "left_right",
    "axes",
    "d_majority_yea",
    "r_majority_yea",
}


def make_card(
    vote_id,
    contest=0.8,
    cross=0.0,
    axes=("fiscal",),
    categories=("Healthcare",),
    side="R",
    date="2025-01-01T12:00:00-05:00",
    omnibus=False,
):
    return {
        "vote_id": vote_id,
        "contest": contest,
        "cross": cross,
        "date": date,
        "omnibus": omnibus,
        "primary_categories": list(categories),
        "axes": [
            {"axis": a, "position": 0.5, "relevance": 0.9, "reasoning": "x"} for a in axes
        ],
        "d_majority_yea": side == "D",
        "r_majority_yea": side == "R",
    }


def full_card():
    return {
        **make_card("h1-119.2025"),
        "spec_hash": "spec",
        "bill_id": "hr1-119",
        "chamber": "house",
        "congress": 119,
        "short_title": "Title",
        "popular_title": None,
        "brief": "This bill does a thing.",
        "key_provisions": ["Does a thing."],
        "needs_two_thirds": False,
        "featured": True,
        "result": "Passed",
        "requires": "1/2",
        "tally": {"D": {"yea": 1, "nay": 2, "not_voting": 0}},
        "sponsor": "Rep. A Person",
        "sponsor_party": "R",
        "source_url": "https://example.com",
        "left_right": {"Healthcare": 0.2},
        "model": "m",
        "schema_version": 5,
        "axis_schema_version": 1,
        "last_modified": "2026-01-01T00:00:00",
    }


def satisfiable_candidates():
    """Plenty of cards for every rule, with both parties' Yea sides well represented."""
    cards = []
    for i, axis in enumerate(AXES * 4):
        side = "D" if i % 2 else "R"
        category = CATEGORIES[i % 2]
        cards.append(
            make_card(f"a{i}", contest=0.9 - i / 100, axes=[axis], categories=[category], side=side)
        )
    for i in range(10):
        cards.append(make_card(f"c{i}", contest=0.5, cross=0.3, side="D" if i % 2 else "R"))
    return cards


def report_for(featured, cards, target):
    return hwyv.build_report(featured, cards, AXES, CATEGORIES, target)


def by_ids(ids, cards):
    wanted = set(ids)
    return [c for c in cards if c["vote_id"] in wanted]


def test_contest_weight():
    assert contest_weight(50, 50) == 1
    assert contest_weight(100, 0) == 0
    assert contest_weight(0, 0) == 0
    assert contest_weight(75, 25) == 0.5


def test_selection_meets_every_rule_when_satisfiable():
    cards = satisfiable_candidates()
    ids = hwyv.select_featured(cards, AXES, CATEGORIES, target=16)
    featured = by_ids(ids, cards)
    report = report_for(featured, cards, 16)

    assert len(ids) == 16
    assert report["unmet"] == []
    assert all(n >= hwyv.MIN_AXIS_CARDS for n in report["counts"]["axes"].values())
    assert all(n >= hwyv.MIN_CATEGORY_CARDS for n in report["counts"]["categories"].values())
    assert report["counts"]["cross"] >= hwyv.MIN_CROSS_CARDS


def test_selection_warns_when_unsatisfiable():
    # Only one trade card, no Immigration cards, nothing cross-party, and only R-Yea
    cards = [make_card(f"f{i}", axes=["fiscal", "social"]) for i in range(8)]
    cards.append(make_card("t0", axes=["trade"]))
    ids = hwyv.select_featured(cards, AXES, CATEGORIES, target=9)
    unmet = report_for(by_ids(ids, cards), cards, 9)["unmet"]

    assert any(rule.startswith("Axis trade: 1 of 3") for rule in unmet)
    assert any(rule.startswith("Category Immigration: 0 of 2") for rule in unmet)
    assert any(rule.startswith("Cross-party") for rule in unmet)
    assert any(rule.startswith("Party balance") for rule in unmet)


def test_low_contest_and_omnibus_cards_are_not_candidates():
    cards = satisfiable_candidates() + [
        make_card("low", contest=0.3, cross=0.5),
        make_card("big", contest=1.0, cross=0.5, omnibus=True),
    ]
    ids = hwyv.select_featured(cards, AXES, CATEGORIES, target=len(cards))
    assert "low" not in ids
    assert "big" not in ids


def test_rank_ties_go_to_newest():
    older = make_card("old", date="2024-01-01T12:00:00-05:00")
    newer = make_card("new", date="2025-01-01T12:00:00-05:00")
    assert [c["vote_id"] for c in hwyv.rank_cards([older, newer])] == ["new", "old"]


def test_balance_swap_never_breaks_axis_coverage():
    # The trade cards are R-Yea, the lowest ranked, and exactly enough for coverage. Swaps would
    # take them first, so they have to skip them and take the other R-Yea cards instead.
    trade = [make_card(f"t{i}", contest=0.45, axes=["trade"], side="R") for i in range(3)]
    fiscal = [
        make_card(f"f{i}", contest=0.8, axes=["fiscal", "social"], side="R") for i in range(9)
    ]
    d_yea = [
        make_card(f"d{i}", contest=0.41, axes=["fiscal", "social"], side="D") for i in range(8)
    ]
    cards = trade + fiscal + d_yea

    selected = by_ids(["t0", "t1", "t2"] + [f"f{i}" for i in range(9)], cards)
    hwyv.balance_parties(selected, hwyv.rank_cards(cards), AXES, [])

    counts = hwyv.coverage_counts(selected, AXES, [])
    assert counts["axes"]["trade"] == 3
    assert hwyv.d_yea_share(selected) >= hwyv.D_YEA_SHARE_RANGE[0]


def test_balance_refuses_a_swap_that_would_break_coverage():
    # Removing either R-Yea card loses axis coverage that no D-Yea card restores
    cards = [
        make_card("r0", axes=["trade"], side="R"),
        make_card("r1", axes=["trade"], side="R"),
        make_card("r2", axes=["trade"], side="R"),
        make_card("d0", contest=0.41, axes=["fiscal"], side="D"),
    ]
    selected = by_ids(["r0", "r1", "r2"], cards)
    hwyv.balance_parties(selected, hwyv.rank_cards(cards), AXES, [])

    assert {c["vote_id"] for c in selected} == {"r0", "r1", "r2"}
    unmet = report_for(selected, cards, 3)["unmet"]
    assert any(rule.startswith("Party balance") for rule in unmet)


def test_overrides_win():
    cards = satisfiable_candidates() + [make_card("low", contest=0.25)]
    selected = hwyv.select_featured(cards, AXES, CATEGORIES, target=10)
    assert "low" not in selected
    excluded = selected[0]

    featured, warnings = hwyv.apply_overrides(
        selected, {"include": ["low"], "exclude": [excluded]}, cards
    )
    assert "low" in featured
    assert excluded not in featured
    assert warnings == []


def test_override_for_unknown_card_warns():
    cards = satisfiable_candidates()
    featured, warnings = hwyv.apply_overrides(
        [], {"include": ["nope"], "exclude": []}, cards
    )
    assert featured == []
    assert "nope" in warnings[0]


def test_overrides_file_is_read_per_chamber(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"house": {"include": ["a"], "exclude": ["b"]}}))

    assert hwyv.load_overrides(path, "house") == {"include": ["a"], "exclude": ["b"]}
    assert hwyv.load_overrides(path, "senate") == {"include": [], "exclude": []}
    assert hwyv.load_overrides(tmp_path / "missing.json", "house") == {
        "include": [],
        "exclude": [],
    }


def test_brief_fields_contain_no_reveal_keys():
    brief = brief_fields(full_card())
    assert set(brief) == set(BRIEF_FIELDS)
    assert not REVEAL_KEYS & set(brief)
    # Ranking fields aren't for display either
    assert "contest" not in brief and "cross" not in brief


def test_card_schema_accepts_a_full_card():
    assert HwyvCard.model_validate(full_card()).vote_id == "h1-119.2025"


def test_compute_vote_stats():
    votes = {
        "Aye": [
            {"id": "a", "party": "D"},
            {"id": "b", "party": "R"},
            {"id": "c", "party": "R"},
            {"id": "d", "party": "R"},
        ],
        "No": [{"id": "e", "party": "D"}, {"id": "f", "party": "D"}, {"id": "g", "party": "R"}],
        "Not Voting": [{"id": "h", "party": "D"}],
        "Present": [{"id": "i", "party": "I"}],
        "Nay": ["VP"],
    }
    stats = hwyv.compute_vote_stats(votes)

    assert stats["tally"]["D"] == {"yea": 1, "nay": 2, "not_voting": 1}
    assert stats["tally"]["R"] == {"yea": 3, "nay": 1, "not_voting": 0}
    assert stats["tally"]["I"] == {"yea": 0, "nay": 0, "not_voting": 1}
    # 4 Yea, 3 Nay: the "VP" string isn't a voter
    assert stats["contest"] == contest_weight(4, 3)
    assert stats["cross"] == 1 / 3
    assert stats["d_majority_yea"] is False
    assert stats["r_majority_yea"] is True


def test_latest_rollcall_per_bill():
    bill = {"congress": 119, "number": 1, "type": "hr"}
    first = {"vote_id": "a", "bill": bill, "date": "2025-01-01", "number": 5}
    second = {"vote_id": "b", "bill": bill, "date": "2025-02-01", "number": 2}
    no_bill = {"vote_id": "c", "bill": {}, "date": "2025-03-01", "number": 3}

    latest = hwyv.latest_rollcall_per_bill([second, first, no_bill])
    assert {k: v["vote_id"] for k, v in latest.items()} == {"hr1-119": "b"}


def test_omnibus():
    five = ["a", "b", "c", "d", "e"]
    assert hwyv.is_omnibus(five, [])
    assert hwyv.is_omnibus(["a"], ["CONSOLIDATED APPROPRIATIONS Act, 2024"])
    assert hwyv.is_omnibus(["a"], [None, "An Omnibus Bill"])
    assert not hwyv.is_omnibus(five[:4], ["Water Resources Act", None])


def test_left_right_scores_match_member_scoring():
    categories = [
        {"name": "A", "partisan_score": 0.5, "impact_score": 0.5},
        {"name": "B", "partisan_score": 0, "impact_score": 0.9},
    ]
    assert hwyv.left_right_scores(categories) == {"A": 0.25}


def test_brief_drops_titles_and_notes_and_cuts_on_a_sentence():
    text = (
        "Some Act of 2025\n\n"
        "(This measure has not been amended since it was passed by the House.)\n\n"
        + "This bill does a thing. " * 40
    )
    brief = hwyv.build_brief(text, "Fallback")

    assert brief.startswith("This bill does a thing.")
    assert "Some Act" not in brief and "measure has not" not in brief
    assert len(brief) <= hwyv.BRIEF_MAX_CHARS
    assert brief.endswith(".")


def test_brief_restores_flattened_list_spacing():
    brief = hwyv.build_brief("This bill helps ports,harbors,and dams.Next it stops.", "F")
    assert brief == "This bill helps ports, harbors, and dams. Next it stops."


def test_brief_falls_back_without_a_summary():
    assert hwyv.build_brief(None, "Fallback") == "Fallback"
    assert hwyv.build_brief("Just A Title", "Fallback") == "Fallback"
