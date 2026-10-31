"""Run a .sql file statement-by-statement against Snowflake and report each result.

Used inside the Snowflake sandbox, where an OAuth session token is provided via
the SNOWFLAKE_TOKEN_FILE_PATH environment variable. No credentials are stored here.

Usage:
    python scripts/run_sql.py sql/01_setup.sql [--role OPSAGENTX_ROLE]
"""
import argparse
import io
import os
import sys

import snowflake.connector


def read_token() -> str:
    """Read a fresh OAuth token (tokens rotate, so never cache them in code)."""
    path = os.environ.get("SNOWFLAKE_TOKEN_FILE_PATH", "/snowflake/session/token")
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def connect(role: str):
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        host=os.environ["SNOWFLAKE_HOST"],
        authenticator="oauth",
        token=read_token(),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        role=role,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sql_file")
    parser.add_argument("--role", default="OPSAGENTX_ROLE")
    args = parser.parse_args()

    with open(args.sql_file, encoding="utf-8") as handle:
        sql_text = handle.read()

    connection = connect(args.role)
    failures = 0
    try:
        # execute_stream understands $$-quoted strings and comments correctly.
        for cursor in connection.execute_stream(io.StringIO(sql_text)):
            first_line = cursor.query.strip().splitlines()[0][:90]
            row = cursor.fetchone()
            print(f"OK   | {first_line} -> {row[0] if row else cursor.rowcount}")
    except snowflake.connector.errors.ProgrammingError as error:
        failures += 1
        print(f"FAIL | {error.msg} (query id {error.sfqid})")
    finally:
        connection.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
