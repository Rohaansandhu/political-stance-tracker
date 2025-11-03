import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
import db.start_mongod as sm

log = logging.getLogger(__name__)

def stop_mongod():
    """Stop mongod cleanly via shutdown command."""
    try:
        client = MongoClient(f"mongodb://localhost:{sm.PORT}/admin", serverSelectionTimeoutMS=2000)
        # Check if mongod is running
        client.admin.command("ping")
        log.info(f"Connected to mongod on port {sm.PORT}. Attempting shutdown...")

        client.admin.command("shutdown")
        log.info("mongod shut down successfully")

    except ConnectionFailure:
        log.error(f"Could not connect to mongod on port {sm.PORT}. Is it running?")
    except OperationFailure as e:
        log.error(f"Failed to shutdown mongod: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stop_mongod()
