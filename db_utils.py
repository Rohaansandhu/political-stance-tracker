from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from start_mongod import PORT
import os

# Specify your own DB name and MONGO_URI in .env or here
MONGO_URI = os.getenv("MONGO_URI", f"mongodb://localhost:{PORT}/")
DB_NAME = os.getenv("DB_NAME", "political_stance_tracker")

COLLECTION_LIST = ["bill_data", "bill_analyses", "votes"]


def get_db():
    """Return a reference to the MongoDB database."""
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


def ensure_indexes():
    """Create unique indexes to avoid duplicate bill entries."""
    db = get_db()
    db.bill_data.create_index([("bill_id", ASCENDING)], unique=True)
    db.bill_analyses.create_index([("bill_id", ASCENDING)], unique=True)
    db.votes.create_index([("vote_id", ASCENDING)], unique=True)


def update_one(collection_name: str, update: dict, filter_key: str):
    """Update a single document in the given collection."""
    db = get_db()
    collection = db[collection_name]
    result = collection.update_one(
        {filter_key: update[filter_key]}, {"$set": update}, upsert=True
    )
    return result.modified_count


def update_many(collection_name: str, updates: list, query: dict):
    """Update multiple documents in the given collection."""
    db = get_db()
    collection = db[collection_name]
    result = collection.update_many(query, {"$set": updates}, upsert=True)
    return result.modified_count
