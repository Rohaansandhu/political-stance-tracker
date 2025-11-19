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