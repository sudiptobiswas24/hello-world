"""
Waits up to two minutes for the database to accept a connection, as the owner (DATABASE_OWNER_URL) where one is
set: the application's own role may not exist until the first migrate makes it.
"""

import os
import time

import psycopg

for attempt in range(60):
    try:
        psycopg.connect(os.environ.get("DATABASE_OWNER_URL") or os.environ["DATABASE_URL"], connect_timeout=3).close()
        break
    except psycopg.OperationalError as error:
        print(f"waiting for the database: {str(error).strip()}", flush=True)
        time.sleep(2)
else:
    raise SystemExit("The database did not answer in two minutes.")
