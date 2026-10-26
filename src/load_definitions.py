"""
Publish the political definition files to the database for the website to read:

  political_definitions/political_categories.json -> `categories` collection
  political_definitions/political_axes.json       -> `axes` collection

Each collection holds exactly one document with the same shape as its file, e.g.
{"political_categories": [...]}. Loading replaces that document, so rerun this whenever
a definition file changes.
"""

import argparse
import json
import time
from pathlib import Path

import db.db_utils as db_utils
from analysis.definitions import load_axes, load_categories
from cloud_db_updater import CLOUD_MONGO_URI, get_cloud_collection

BACKUP_DIR = Path("data/definition_backups")


def build_documents():
    """collection name -> the document to store (validates the definition files on load)."""
    return {
        "categories": load_categories(),
        "axes": {"political_axes": load_axes()},
    }


def replace_document(collection, document, backup_label):
    """Make `document` the only document in `collection`, saving the old one first."""
    existing = list(collection.find({}))
    if len(existing) > 1:
        raise ValueError(
            f"{collection.name} has {len(existing)} documents; expected at most 1. "
            "Resolve this by hand before loading."
        )

    if existing:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{backup_label}_{collection.name}_{int(time.time())}.json"
        backup.write_text(json.dumps(existing[0], default=str, indent=2))
        print(f"  backed up the old document to {backup}")

    # replace_one keeps the existing document's _id
    collection.replace_one({}, document, upsert=True)


def load(target_name, get_collection, documents, dry_run):
    print(f"\n{target_name}:")
    for name, document in documents.items():
        collection = get_collection(name)
        count = collection.count_documents({})
        print(f"- {name}: {count} existing document(s)")
        if dry_run:
            print("  dry run, not writing")
            continue
        replace_document(collection, document, target_name)
        print(f"  replaced with {len(next(iter(document.values())))} entries")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["local", "cloud", "both"],
        default="local",
        help="Which database to load into (default: local)",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Report what would be replaced without writing",
    )
    args = parser.parse_args()

    documents = build_documents()

    if args.target in ("local", "both"):
        load("local", db_utils.get_collection, documents, args.dry_run)

    if args.target in ("cloud", "both"):
        if not CLOUD_MONGO_URI:
            raise ValueError("CLOUD DB URI NOT FOUND!!!")
        load("cloud", get_cloud_collection, documents, args.dry_run)
