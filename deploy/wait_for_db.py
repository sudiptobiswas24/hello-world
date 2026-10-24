"""Waits up to two minutes for DATABASE_URL to accept a connection."""

import os
import time

import psycopg

for attempt in range(60):
    try:
        psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3).close()
        break
    except psycopg.OperationalError as error:
        print(f"waiting for the database: {str(error).strip()}", flush=True)
        time.sleep(2)
else:
    raise SystemExit("The database did not answer in two minutes.")
