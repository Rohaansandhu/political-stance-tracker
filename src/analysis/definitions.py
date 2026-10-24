"""Loaders for the political definition files (categories and axes).

Categories are issue topics; axes are viewpoints that cut across categories.
Loading validates the files against each other so they can't silently drift.
"""

import json
from pathlib import Path

DEFINITIONS_DIR = Path("political_definitions")

# An axis must apply to at least this many categories, otherwise it is really a topic
MIN_AXIS_CATEGORIES = 3


def _load_json(filename):
    with open(DEFINITIONS_DIR / filename, "r") as f:
        return json.load(f)


def load_categories():
    """Full category definitions (with scopes and left_right pole views)."""
    return _load_json("political_categories.json")


def load_reduced_categories():
    """Shorter category definitions, used when the full set exceeds a model's context."""
    return _load_json("reduced_political_categories.json")


def category_names(categories=None):
    """Names of all top-level categories."""
    categories = categories or load_categories()
    return [c["name"] for c in categories["political_categories"]]


def load_axes():
    """All axis definitions, validated against the category file."""
    axes = _load_json("political_axes.json")["political_axes"]
    _validate_axes(axes, set(category_names()))
    return axes


def llm_axes(axes=None):
    """Axes scored per bill by an LLM (excludes derived and computed axes)."""
    axes = axes or load_axes()
    return [a for a in axes if a["source"] == "llm"]


def _validate_axes(axes, valid_categories):
    seen_ids = set()
    for axis in axes:
        axis_id = axis["id"]
        if axis_id in seen_ids:
            raise ValueError(f"Duplicate axis id: {axis_id}")
        seen_ids.add(axis_id)

        typical = axis["typical_categories"]
        listed = set(typical["most"]) | set(typical["some"])

        unknown = listed - valid_categories
        if unknown:
            raise ValueError(
                f"Axis {axis_id!r} references unknown categories: {sorted(unknown)}"
            )

        # Computed axes apply to every roll call, not to specific categories
        if not axis["computed"] and len(listed) < MIN_AXIS_CATEGORIES:
            raise ValueError(
                f"Axis {axis_id!r} applies to {len(listed)} categories; "
                f"needs at least {MIN_AXIS_CATEGORIES}"
            )
