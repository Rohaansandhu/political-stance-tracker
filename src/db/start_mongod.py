import os
import subprocess
import logging
from pathlib import Path

# ---- Configuration ----
PORT = 27017 
DB_PATH = Path("data/db")
LOG_PATH = Path("data/mongodlogs/mongod.log")
# optional, uncomment for proper replication
REPLICA_SET_NAME = "LocalReplica0"  
REPLICA_SET_NAME = None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def ensure_dirs():
    DB_PATH.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def start_mongod():
    ensure_dirs()

    cmd = [
        "mongod",
        "--port", str(PORT),
        "--bind_ip", "localhost",
        "--dbpath", str(DB_PATH),
        "--logpath", str(LOG_PATH),
    ]

    # Fork for only Linux
    if os.name == "posix" and os.uname().sysname != "Darwin":
        cmd.extend(["--fork"])

    # Enable replica set if configured
    if REPLICA_SET_NAME:
        cmd.extend(["--replSet", REPLICA_SET_NAME])

    log.info(f"Starting mongod on port {PORT} (dbpath={DB_PATH})...")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    # Check if mongod started successfully
    if result.returncode == 48:  
        log.info("mongod already running")
    elif result.returncode != 0:
        log.error("Error starting mongod. Check log for details.")
        log.error(result.stdout.decode())
        return

    # Initialize replica set (optional)
    if REPLICA_SET_NAME:
        log.info("Initializing replica set...")
        init_cmd = [
            "mongosh",
            "--port", str(PORT),
            "--eval", f"rs.initiate({{ _id: '{REPLICA_SET_NAME}', members: [{{ _id: 0, host: 'localhost:{PORT}' }}] }})"
        ]
        init_result = subprocess.run(init_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = init_result.stdout.decode("utf-8")

        if '"codeName" : "AlreadyInitialized"' in output:
            log.info("Replica Set already initialized")
        elif init_result.returncode != 0:
            log.error("Error initiating Replica Set")
            log.error(output)
        else:
            log.info("Replica Set started successfully")


if __name__ == "__main__":
    start_mongod()
