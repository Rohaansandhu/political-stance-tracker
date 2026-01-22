import argparse
import db.db_utils as db_utils
from db.db_utils import DB_NAME
from pymongo import UpdateOne, MongoClient
from dotenv import load_dotenv
import os
import time

# Load env variables
load_dotenv()

# Cloud DB connection
CLOUD_MONGO_URI = os.getenv("CLOUD_URI", None)


def get_cloud_db():
    client = MongoClient(CLOUD_MONGO_URI)
    return client[DB_NAME]


def get_cloud_collection(collection_name: str):
    cloud_db = get_cloud_db()
    return cloud_db[collection_name]


def list_of_strings(arg_value):
    """Converts a comma-separated string into a list of strings."""
    return [s.strip() for s in arg_value.split(",")]


def sync_local_to_cloud(
    local_collection_name: str, cloud_collection_name: str, key_fields
):
    """
    Sync documents from the local DB to the cloud DB.

    - Inserts documents that do not exist in the cloud.
    - Updates cloud documents only if local last_modified is newer.
    - key_fields: list of fields used to identify documents (compound keys supported)
    """

    local_collection = db_utils.get_collection(local_collection_name)
    cloud_collection = get_cloud_collection(cloud_collection_name)

    # Ensure key_fields is a list
    if isinstance(key_fields, str):
        key_fields = [key_fields]

    # Fetch all documents from local DB
    local_docs = list(local_collection.find({}))
    cloud_docs = list(cloud_collection.find({}))

    # Make a tuple using the key_fields for finds
    cloud_index = {
        tuple(doc[field] for field in key_fields): doc
        for doc in cloud_docs
        if all(field in doc for field in key_fields)
    }

    actions = []

    for doc in local_docs:

        # Make sure every doc has the required key_fields
        if not all(field in doc for field in key_fields):
            continue

        # Build filter to identify the document in cloud DB
        key = tuple(doc[field] for field in key_fields)
        filter_doc = {field: doc[field] for field in key_fields}

        # Try to find the document in the cloud using our previous indexes
        cloud_doc = cloud_index.get(key)

        if cloud_doc is None:
            # Document doesn't exist in cloud — insert it
            actions.append(UpdateOne(filter_doc, {"$set": doc}, upsert=True))
        else:
            # Document exists — compare last_modified
            local_ts = doc.get("last_modified")
            cloud_ts = cloud_doc.get("last_modified")

            if not local_ts:
                print(
                    f"Warning: local doc {filter_doc} has no last_modified; skipping."
                )
                continue

            # Compare timestamps: update if local is newer
            # Quick fix, last_modified is a string in the cloud db
            if not cloud_ts or isinstance(cloud_ts, str) or local_ts > cloud_ts:
                actions.append(UpdateOne(filter_doc, {"$set": doc}, upsert=True))

    if actions:
        start_time = time.time()
        result = cloud_collection.bulk_write(actions)
        end_time = time.time()
        total_time = end_time - start_time
        print(f"Time taken: {total_time} seconds for {len(actions)} operations")
        print(
            f"Synced {result.modified_count + result.upserted_count} documents to cloud '{cloud_collection_name}'."
        )
    else:
        print(f"No updates required for cloud collection '{cloud_collection_name}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local_collection",
        required=True,
        help="Specify the name of the local collection used to update the cloud",
    )
    parser.add_argument(
        "--cloud_collection",
        required=True,
        help="Specify the name of the cloud collection to update",
    )
    parser.add_argument(
        "--key_fields",
        required=True,
        help="Specify the key fields of the collection. These are the unique fields that tell the cloud db which document to update. Specify in a comma seperated list.",
    )
    args = parser.parse_args()

    # Parse strings using commas
    key_fields = list_of_strings(args.key_fields)

    if not CLOUD_MONGO_URI:
        raise ValueError("CLOUD DB URI NOT FOUND!!!")

    # Edit this with the collections to update
    sync_local_to_cloud(
        local_collection_name=args.local_collection,
        cloud_collection_name=args.cloud_collection,
        key_fields=key_fields,
    )
