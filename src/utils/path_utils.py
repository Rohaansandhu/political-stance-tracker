"""Helpers for naming and creating output files."""

import re
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """Replace invalid filename characters with underscores."""
    return re.sub(r"[^\w\-_.]", "_", name)


def get_output_dir(base_dir, spec_hash) -> Path:
    """Construct and ensure `base_dir/<spec_hash>` exists."""
    output_dir = Path(base_dir) / sanitize_filename(spec_hash)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
