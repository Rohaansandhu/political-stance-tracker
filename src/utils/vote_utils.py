"""Helpers for interpreting individual vote records."""


def build_bill_id(bill: dict) -> str:
    """
    Turn a bill object into a string for id.
    Example: {"congress":119,"number":242,"type":"hres"}
             -> "hres242-119"
    """
    if bill == {}:
        return ""
    congress = bill["congress"]
    number = bill["number"]
    btype = bill["type"]
    return "%s%s-%s" % (btype, number, congress)


def get_vote_value(vote):
    """Convert vote string to multiplier."""
    vote_lower = vote.lower().strip()

    # Support votes
    if vote_lower in {"yea", "yes", "aye", "y"}:
        return 1

    # Opposition votes
    if vote_lower in {"nay", "no", "n"}:
        return -1

    # Didn't vote
    return 0
