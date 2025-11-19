from dotenv import load_dotenv
import pymongo
from pymongo import DESCENDING, ASCENDING, MongoClient
from db.start_mongod import PORT
import os

# Load environment variables once when module is imported
load_dotenv()

# Specify your own DB name and MONGO_URI in .env or here
MONGO_URI = os.getenv("MONGO_URI", f"mongodb://localhost:{PORT}/")
DB_NAME = os.getenv("DB_NAME", "political_stance_tracker")

COLLECTION_LIST = [
    "bill_data",
    "bill_analyses",
    "rollcall_votes",
    "member_votes",
    "legislator_profiles",
]


def get_db():
    """Return a reference to the MongoDB database."""
    client: MongoClient = MongoClient(MONGO_URI)
    return client[DB_NAME]


def ensure_indexes():
    """Create unique indexes to avoid duplicate bill entries."""
    db = get_db()
    db.bill_data.create_index([("bill_id", ASCENDING)], unique=True)
    db.bill_analyses.create_index(
        [("bill_id", ASCENDING), ("model", ASCENDING), ("schema_version", DESCENDING)],
        unique=True,
    )
    db.legislator_profiles.create_index(
        [
            ("member_id", ASCENDING),
            ("model", ASCENDING),
            ("schema_version", DESCENDING),
            ("spec_hash", ASCENDING),
        ],
        unique=True,
    )
    db.member_votes.create_index([("member_id", ASCENDING)], unique=True)
    db.rollcall_votes.create_index([("vote_id", ASCENDING)], unique=True)


def update_one(collection_name, document, key_fields):
    """
    key_fields can be a string or list of strings for compound keys
    """
    collection = get_collection(collection_name)

    if isinstance(key_fields, str):
        key_fields = [key_fields]

    # Build filter from key fields
    filter = {field: document[field] for field in key_fields}
    collection.update_one(filter, {"$set": document}, upsert=True)


def update_many(collection_name: str, updates: list, query: dict):
    """Update multiple documents in the given collection."""
    db = get_db()
    collection = db[collection_name]
    result = collection.update_many(query, {"$set": updates}, upsert=True)
    return result.modified_count


def get_collection(collection_name: str):
    """Return a reference to the specified collection."""
    db = get_db()
    return db[collection_name]


def bulk_write(collection: str, actions: list):
    """Bulk write `actions` into `collection` in order of `actions`"""
    db = get_db()
    return db[collection].bulk_write(actions)


# Use utils as a script to ensure indexes in database (only needs to be run once)
if __name__ == "__main__":
    ensure_indexes()
