import copy

from pydantic import BaseModel

# Bill Summary may be removed in the future (since Library of Congress already writes this)
# Key provisions will likely be kept, and no longer nested
class BillSummary(BaseModel):
    title: str
    key_provisions: list[str]
    # controversy_level: str
    # partisan_divide: str

# Voting Analysis will likely be kept, or refined, because stakeholder_support is definitely, definitely necessary
class Vote(BaseModel):
    political_position: str
    philosophy: str
    stakeholder_support: list[str]
    reasoning: str

class VotingAnalysis(BaseModel):
    yes_vote: Vote
    no_vote: Vote

class Category(BaseModel):
    name: str
    partisan_score: float
    impact_score: float
    reasoning: str

class PoliticalCategories(BaseModel):
    primary_categories: list[Category]
    subcategories: list[Category]

class BillAnalysis(BaseModel):
    bill_summary: BillSummary
    political_categories: PoliticalCategories
    voting_analysis: VotingAnalysis

class AxisScore(BaseModel):
    axis: str  # id from political_axes.json, never a computed axis such as "institutional"
    position: float  # -1..1, direction of a YES vote
    relevance: float  # 0..1
    reasoning: str

class AxisAnalysis(BaseModel):
    axis_scores: list[AxisScore]


# --- Structured Outputs (OpenAI strict JSON schema) ---

def _make_strict(node):
    """
    Rewrite a pydantic JSON schema in place to meet OpenAI strict mode: every object
    lists all its properties as required and forbids extras. Titles/defaults are dropped.
    """
    if isinstance(node, dict):
        # Metadata keys are strings; a *property* named "title" holds a schema (dict)
        for key in ("title", "default"):
            if not isinstance(node.get(key), dict):
                node.pop(key, None)
        if "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for item in node:
            _make_strict(item)
    return node


def _response_format(name, schema):
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def category_response_format(primary_names, subcategory_names):
    """
    Strict `response_format` for BillAnalysis. Category and subcategory names are enums,
    so the model can't return a misspelled or invented name.
    """
    schema = _make_strict(BillAnalysis.model_json_schema())
    defs = schema["$defs"]
    category_schema = defs.pop("Category")

    for field, def_name, names in (
        ("primary_categories", "PrimaryCategory", primary_names),
        ("subcategories", "SubCategory", subcategory_names),
    ):
        named = copy.deepcopy(category_schema)
        named["properties"]["name"]["enum"] = list(names)
        defs[def_name] = named
        defs["PoliticalCategories"]["properties"][field]["items"] = {
            "$ref": f"#/$defs/{def_name}"
        }

    return _response_format("bill_analysis", schema)


def axis_response_format(axis_ids):
    """Strict `response_format` for AxisAnalysis, with the axis id as an enum."""
    schema = _make_strict(AxisAnalysis.model_json_schema())
    schema["$defs"]["AxisScore"]["properties"]["axis"]["enum"] = list(axis_ids)
    return _response_format("axis_analysis", schema)


# --- Amendment analyses (generate_amendment_analysis.py) ---

AMENDMENT_KINDS = ("substantive", "technical", "pro_forma", "en_bloc")


class AmendmentAnalysis(BaseModel):
    kind: str  # one of AMENDMENT_KINDS
    summary: str  # one sentence: what a YES vote changes
    primary_categories: list[Category]
    axis_scores: list[AxisScore]


def amendment_response_format(primary_names, axis_ids):
    """
    Strict `response_format` for AmendmentAnalysis. The kind, category names, and axis ids
    are enums, so the model can't invent one.
    """
    schema = _make_strict(AmendmentAnalysis.model_json_schema())
    defs = schema["$defs"]
    schema["properties"]["kind"]["enum"] = list(AMENDMENT_KINDS)
    defs["Category"]["properties"]["name"]["enum"] = list(primary_names)
    defs["AxisScore"]["properties"]["axis"]["enum"] = list(axis_ids)
    return _response_format("amendment_analysis", schema)
