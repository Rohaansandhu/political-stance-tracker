# Script to update the cloud db(s), attempt to only add changes from local to cloud. 
# CANNOT delete data, must find a way to track changes after a certain timestamp
# Here are the cases we need to account for
# Insert document into collection if it doesn't exist in the cloud db.
# If there is a matching document in the cloud db, check last_modified
# If last_modified in the cloud is "earlier" than the last_modified in the local db, update with the local db version
# Otherwise, ignore, as the local db has a more outdated version
import db.db_utils as db_utils
from db.db_utils import DB_NAME
from pymongo import UpdateOne, MongoClient
from dotenv import load_dotenv
import os
import time

# Cloud DB connection
CLOUD_MONGO_URI = os.getenv("CLOUD_URI", None)

def get_cloud_db():
    client = MongoClient(CLOUD_MONGO_URI)
    return client[DB_NAME]

def get_cloud_collection(collection_name: str):
    cloud_db = get_cloud_db()
    return cloud_db[collection_name]


def sync_local_to_cloud(local_collection_name: str, cloud_collection_name: str, key_fields):
    """
    Sync documents from the local DB to the cloud DB.
    
    - Inserts documents that do not exist in the cloud.
    - Updates cloud documents only if local last_modified is newer.
    - key_fields: list of fields used to identify documents (compound keys supported)
    """

    local_collection = db_utils.get_collection(local_collection_name)
    cloud_collection = get_cloud_collection(cloud_collection_name)

    # Fetch all documents from local DB
    local_docs = list(local_collection.find({}))
    actions = []

    for doc in local_docs:
        # Ensure key_fields is a list
        if isinstance(key_fields, str):
            key_fields_list = [key_fields]
        else:
            key_fields_list = key_fields

        # Build filter to identify the document in cloud DB
        filter_doc = {field: doc[field] for field in key_fields_list}

        # Try to find the document in the cloud
        cloud_doc = cloud_collection.find_one(filter_doc)

        if cloud_doc is None:
            # Document doesn't exist in cloud — insert it
            actions.append(UpdateOne(
                filter_doc,
                {"$set": doc},
                upsert=True
            ))
        else:
            # Document exists — compare last_modified
            local_ts = doc.get("last_modified")
            cloud_ts = cloud_doc.get("last_modified")

            if not local_ts:
                print(f"Warning: local doc {filter_doc} has no last_modified; skipping.")
                continue

            # Compare timestamps: update if local is newer
            # Quick fix, last_modified is a string in the cloud db
            if not cloud_ts or isinstance(cloud_ts, str) or local_ts > cloud_ts:
                actions.append(UpdateOne(
                    filter_doc,
                    {"$set": doc},
                    upsert=True
                ))

    if actions:
        result = cloud_collection.bulk_write(actions)
        print(f"Synced {result.modified_count + result.upserted_count} documents to cloud '{cloud_collection_name}'.")
    else:
        print(f"No updates required for cloud collection '{cloud_collection_name}'.")


if __name__ == "__main__":
    if not CLOUD_MONGO_URI:
        raise ValueError("CLOUD DB URI NOT FOUND!!!")

    # Edit this with the collections to update
    sync_local_to_cloud(
        local_collection_name="legislator_profiles",
        cloud_collection_name="legislator_profiles",
        key_fields=["spec_hash", "member_id"]
    )
