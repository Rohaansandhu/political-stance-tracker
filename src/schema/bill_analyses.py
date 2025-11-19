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
    bill_id: str
    bill_summary: BillSummary
    bill_truncated: bool
    last_modified: str
    political_categories: PoliticalCategories
    # political_spectrums: object
    schema_version: int
    voting_analysis: VotingAnalysis
    model: str
    bill_number: int
    bill_type: str
    chamber: str
    congress: str