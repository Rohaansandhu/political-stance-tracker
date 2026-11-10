"""Tests for the amendment pipeline (get_amendments.py, amendment_analysis_client.py)."""

import json
import os
from pathlib import Path

import pytest

# llm_common builds an API client at import time
os.environ.setdefault("CLIENT", "openai")
os.environ.setdefault("OPENAI_API_KEY", "test")

import analysis.amendment_analysis_client as amd_client  # noqa: E402
import analysis.batch_runner as batch_runner  # noqa: E402
from get_amendments import (  # noqa: E402
    build_amendment_id,
    build_vote_id,
    extract_vote_ids,
    get_vote_direction,
    match_votes,
    sponsor_matches,
)
from utils.vote_utils import build_bill_id  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def repo_cwd(monkeypatch):
    # Definition files are loaded relative to the repo root
    monkeypatch.chdir(REPO_ROOT)


# --- vote ids and the join ---


def test_build_vote_id_house():
    recorded = {
        "chamber": "House",
        "congress": 118,
        "rollNumber": 75,
        "sessionNumber": 2,
        "date": "2024-03-08T15:35:43Z",
    }
    assert build_vote_id(recorded) == "h75-118.2024"


def test_build_vote_id_senate():
    recorded = {
        "chamber": "Senate",
        "congress": 118,
        "rollNumber": 145,
        "sessionNumber": 2,
        "date": "2024-04-20T02:07:00Z",
    }
    assert build_vote_id(recorded) == "s145-118.2024"


def test_build_vote_id_uses_eastern_year():
    # 11:30pm Eastern on Dec 31 is already Jan 1 in UTC
    recorded = {"chamber": "Senate", "congress": 117, "rollNumber": 9, "date": "2022-01-01T04:30:00Z"}
    assert build_vote_id(recorded) == "s9-117.2021"


def test_duplicate_recorded_votes_give_one_vote_id():
    vote = {"chamber": "House", "congress": 118, "rollNumber": 75, "date": "2024-03-08T15:35:43Z"}
    actions = [
        {"text": "On agreeing to the Lynch amendment (A008) Failed", "recordedVotes": [vote]},
        {"recordedVotes": [dict(vote)]},
        {"text": "Amendment (A008) offered by Mr. Lynch."},
    ]
    assert extract_vote_ids(actions) == ["h75-118.2024"]


def test_sponsor_matches_roll_call_author():
    rollcall = {"amendment": {"author": "D\u2019Esposito of New York Part B Amendment No. 1"}}
    assert sponsor_matches(rollcall, {"sponsors": [{"lastName": "D'Esposito"}]}) is True
    assert sponsor_matches(rollcall, {"sponsors": [{"lastName": "Lynch"}]}) is False
    assert sponsor_matches({"amendment": {"number": 1834}}, {"sponsors": []}) is None


def test_vote_shared_by_two_amendments_is_en_bloc_and_dropped():
    matched, shared = match_votes(
        {
            "hamdt1-118": ["h10-118.2024", "h11-118.2024"],
            "hamdt2-118": ["h11-118.2024"],
            "hamdt3-118": ["h99-118.2024"],  # not an amendment roll call we track
        },
        {"h10-118.2024", "h11-118.2024", "h12-118.2024"},
    )
    assert matched == {"h10-118.2024": "hamdt1-118"}
    assert shared == {"h11-118.2024": ["hamdt1-118", "hamdt2-118"]}


def test_vote_direction():
    assert get_vote_direction({"question": "On Agreeing to the Amendment", "type": "On the Amendment"}) == 1
    assert get_vote_direction({"question": "On the Motion to Table S.Amdt. 5", "type": "On Motion to Table"}) == -1


def test_amendment_ids_match_build_bill_id():
    assert build_bill_id({"type": "hamdt", "number": 858, "congress": 118}) == "hamdt858-118"
    assert build_amendment_id("HAMDT", "858", 118) == "hamdt858-118"
    assert build_amendment_id("SAMDT", "1834", "118") == "samdt1834-118"


# --- analysis client ---


def valid_response(**overrides):
    response = {
        "kind": "substantive",
        "summary": "Requires an SEC certification before the bill takes effect.",
        "primary_categories": [
            {
                "name": amd_client.primary_category_names()[0],
                "partisan_score": -0.5,
                "impact_score": 0.4,
                "reasoning": "Adds a regulatory check.",
            }
        ],
        "axis_scores": [
            {"axis": "economic", "position": -0.6, "relevance": 0.8, "reasoning": "More regulation."}
        ],
    }
    response.update(overrides)
    return json.dumps(response)


def test_parse_response_valid():
    result, error, bad = amd_client.parse_response(valid_response())
    assert error is None and bad == []
    assert result["kind"] == "substantive"
    assert result["political_categories"]["subcategories"] == []
    assert result["political_categories"]["primary_categories"][0]["partisan_score"] == -0.5
    assert result["axis_scores"][0]["axis"] == "economic"


def test_parse_response_drops_zero_position_axes():
    response = valid_response(
        axis_scores=[
            {"axis": "economic", "position": -0.4, "relevance": 0.8, "reasoning": "x"},
            {"axis": "fiscal", "position": 0.0, "relevance": 0.5, "reasoning": "mixed"},
        ]
    )
    result, error, _ = amd_client.parse_response(response)
    assert error is None
    assert [s["axis"] for s in result["axis_scores"]] == ["economic"]


def test_parse_response_unknown_axis():
    response = valid_response(
        axis_scores=[{"axis": "institutional", "position": 0.1, "relevance": 0.5, "reasoning": "x"}]
    )
    assert amd_client.parse_response(response) == (None, "bad_axis", ["institutional"])


def test_parse_response_unknown_category():
    response = valid_response(
        primary_categories=[
            {"name": "Underwater Basket Weaving", "partisan_score": 0, "impact_score": 0.1, "reasoning": "x"}
        ]
    )
    result, error, bad = amd_client.parse_response(response)
    assert (result, error, bad) == (None, "bad_category", ["Underwater Basket Weaving"])


def test_parse_response_bad_kind_and_bad_json():
    assert amd_client.parse_response(valid_response(kind="vibes"))[1] == "bad_format"
    assert amd_client.parse_response("not json")[1] == "bad_format"


def test_parse_response_clamps_out_of_range():
    name = amd_client.primary_category_names()[0]
    response = valid_response(
        primary_categories=[{"name": name, "partisan_score": -3, "impact_score": 2, "reasoning": "x"}],
        axis_scores=[{"axis": "economic", "position": 1.7, "relevance": -1, "reasoning": "x"}],
    )
    result, error, _ = amd_client.parse_response(response)
    assert error is None
    category = result["political_categories"]["primary_categories"][0]
    assert (category["partisan_score"], category["impact_score"]) == (-1.0, 1.0)
    axis = result["axis_scores"][0]
    assert (axis["position"], axis["relevance"]) == (1.0, 0.0)


def test_parse_response_allows_empty_scores_for_non_substantive():
    result, error, _ = amd_client.parse_response(
        valid_response(kind="pro_forma", primary_categories=[], axis_scores=[])
    )
    assert error is None and result["kind"] == "pro_forma"


def make_item(**overrides):
    item = {
        "bill_id": "hamdt858-118",
        "amendment_text": "Amendment sought to require an SEC certification.",
        "input_source": "purpose",
        "bill_title": "Expanding Access to Capital Act of 2023",
        "bill_text": "This bill makes reforms to capital markets.",
        "category_tags": ["Economy & Finance"],
        "amended_amendment_purpose": None,
    }
    item.update(overrides)
    return item


def test_messages_never_include_sponsor_and_system_is_shared():
    first = amd_client.build_messages(make_item())
    second = amd_client.build_messages(make_item(amendment_text="Something else entirely."))
    assert first[0] == second[0]
    user = first[1]["content"]
    assert user.startswith(amd_client.USER_INSTRUCTIONS)
    assert user.endswith("AMENDMENT:\nAmendment sought to require an SEC certification.")
    assert "SECOND-DEGREE" not in user

    second_degree = amd_client.build_messages(make_item(amended_amendment_purpose="Strike section 2."))
    assert "SECOND-DEGREE" in second_degree[1]["content"]


def test_parent_summary_is_truncated():
    item = make_item(bill_text="x" * (amd_client.MAX_PARENT_CHARS + 100))
    user = amd_client.build_messages(item)[1]["content"]
    assert "[Bill summary truncated]" in user
    assert amd_client.finalize({}, item)["bill_truncated"] is True


def test_response_format_enums():
    from schema.bill_analyses import amendment_response_format

    fmt = amendment_response_format(["A", "B"], ["economic"])
    schema = fmt["json_schema"]["schema"]
    assert schema["properties"]["kind"]["enum"] == ["substantive", "technical", "pro_forma", "en_bloc"]
    assert schema["$defs"]["Category"]["properties"]["name"]["enum"] == ["A", "B"]
    assert schema["$defs"]["AxisScore"]["properties"]["axis"]["enum"] == ["economic"]
    assert schema["additionalProperties"] is False


# --- batch runner compatibility ---


def test_batch_custom_id_round_trips():
    spec = batch_runner.PassSpec(
        name="amd",
        model="test-model",
        version=amd_client.AMENDMENT_SCHEMA_VERSION,
        version_field="amendment_schema_version",
        output_collection="amendment_analyses",
        build_messages=amd_client.build_messages,
        response_format=amd_client.get_response_format,
        parse_response=amd_client.parse_response,
        finalize=amd_client.finalize,
        save=lambda item, analysis: None,
        load_items=lambda ids: [],
        select_items=lambda *args: [],
        analyze_sync=lambda item: None,
        expected_output_tokens=500,
    )
    request = batch_runner.build_request(spec, make_item(), 0)
    assert request["custom_id"] == "hamdt858-118|amd|0"
    assert batch_runner.parse_custom_id(request["custom_id"]) == ("hamdt858-118", "amd", 0)


# --- profiles (calc_member_ideology regression, calc_amendment_ideology) ---

import copy  # noqa: E402

import calc_amendment_ideology as cai  # noqa: E402
import calc_member_ideology as cmi  # noqa: E402

PASSAGE_VOTES = [
    {"vote_id": "h1-118.2023", "bill": {"type": "hr", "number": 1, "congress": 118},
     "vote": "Yea", "member_id": "M1", "date": "2023-03-01"},
    {"vote_id": "h2-118.2023", "bill": {"type": "hr", "number": 2, "congress": 118},
     "vote": "Nay", "member_id": "M1", "date": "2023-04-01"},
]
BILL_ANALYSES = {
    "hr1-118": {"political_categories": {
        "primary_categories": [{"name": "Economy & Finance", "partisan_score": 0.5, "impact_score": 0.8}],
        "subcategories": [{"name": "Taxes", "partisan_score": 0.4, "impact_score": 0.5}]}},
    "hr2-118": {"political_categories": {
        "primary_categories": [{"name": "Healthcare", "partisan_score": -0.6, "impact_score": 0.7}],
        "subcategories": []}},
}
AXIS_ANALYSES = {
    "hr1-118": {"axis_scores": [{"axis": "economic", "position": 0.5, "relevance": 0.9}]},
    "hr2-118": {"axis_scores": [{"axis": "social", "position": -0.4, "relevance": 0.6}]},
}
AMENDMENT_ANALYSIS = {
    "kind": "substantive",
    "political_categories": {
        "primary_categories": [{"name": "Economy & Finance", "partisan_score": -0.7, "impact_score": 0.8}],
        "subcategories": []},
    "axis_scores": [{"axis": "economic", "position": -0.7, "relevance": 0.8}],
}
AMENDMENT_VOTE = {"vote_id": "h75-118.2024", "bill": {"type": "hamdt", "number": 858, "congress": 118},
                  "vote": "No", "member_id": "M1", "date": "2024-03-08"}


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find(self, query):
        self.queries.append(query)
        return iter([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])


def old_inline_query(collection, member_id):
    """What process_all_legislators did before gather_legislator_votes existed."""
    return collection.find({"member_id": member_id})


def test_gather_legislator_votes_defaults_match_old_query(monkeypatch):
    docs = PASSAGE_VOTES + [dict(PASSAGE_VOTES[0], member_id="M2")]
    collection = FakeCollection(docs)
    monkeypatch.setattr(cmi.db_utils, "get_collection", lambda name: collection)

    assert cmi.gather_legislator_votes("M1") == list(old_inline_query(collection, "M1"))
    assert collection.queries[0] == {"member_id": "M1"}


def test_gather_legislator_votes_extras_and_no_db(monkeypatch):
    collection = FakeCollection(PASSAGE_VOTES)
    monkeypatch.setattr(cmi.db_utils, "get_collection", lambda name: collection)
    extras = {"M1": [AMENDMENT_VOTE]}

    assert cmi.gather_legislator_votes("M1", extras) == PASSAGE_VOTES + [AMENDMENT_VOTE]
    assert cmi.gather_legislator_votes("M1", extras, include_db_votes=False) == [AMENDMENT_VOTE]
    assert cmi.gather_legislator_votes("M2", extras) == []


def test_ideology_unchanged_without_extras_and_only_adds_amendments():
    baseline = cmi.calculate_legislator_ideology(iter(PASSAGE_VOTES), BILL_ANALYSES, AXIS_ANALYSES)
    # A list (what gather_legislator_votes returns) gives the same result as a cursor
    assert cmi.calculate_legislator_ideology(list(PASSAGE_VOTES), BILL_ANALYSES, AXIS_ANALYSES) == baseline

    combined_bills = {**BILL_ANALYSES, "hamdt858-118": AMENDMENT_ANALYSIS}
    combined_axes = {**AXIS_ANALYSES, "hamdt858-118": AMENDMENT_ANALYSIS}
    # Amendment analyses present but no amendment votes: identical output
    assert cmi.calculate_legislator_ideology(list(PASSAGE_VOTES), combined_bills, combined_axes) == baseline

    combined = cmi.calculate_legislator_ideology(
        PASSAGE_VOTES + [AMENDMENT_VOTE], combined_bills, combined_axes
    )
    assert combined["vote_count"] == baseline["vote_count"] + 1
    economy = combined["primary_category_classifications"]["Economy & Finance"]
    assert economy["bill_count"] == 2
    # NO on a liberal (-0.7) amendment pushes conservative: -0.7 * 0.8 * -1
    assert economy["score"] == round((0.5 * 0.8 + 0.56) / 2, 3)
    assert combined["axis_classifications"]["economic"]["bill_count"] == 2
    # Untouched categories and axes are exactly as before
    assert combined["primary_category_classifications"]["Healthcare"] == \
        baseline["primary_category_classifications"]["Healthcare"]
    assert combined["axis_classifications"]["social"] == baseline["axis_classifications"]["social"]
    assert combined["subcategory_classifications"] == baseline["subcategory_classifications"]


def test_amendment_vote_records_shape():
    rollcall = {
        "vote_id": "h75-118.2024",
        "date": "2024-03-08T10:31:00-05:00",
        "votes": {
            "Aye": [{"id": "L000562", "party": "D"}, "VP"],
            "No": [{"id": "A000055", "party": "R"}, {"party": "R"}],
        },
    }
    amendment = {"type": "hamdt", "number": 858, "congress": "118"}
    records = cai.amendment_vote_records(rollcall, amendment, {"L000562": "L000562"})
    assert [(r["member_id"], r["vote"]) for r in records] == [("L000562", "Aye"), ("A000055", "No")]
    assert build_bill_id(records[0]["bill"]) == "hamdt858-118"


def test_amendment_spec_hash():
    base = "gpt-6-luna_5_ax1_all_house_all"
    assert cai.get_amendment_spec_hash(base, 1, "amendments") == base + "_amd1-only"
    assert cai.get_amendment_spec_hash(base, 1, "combined") == base + "_amd1-combined"
