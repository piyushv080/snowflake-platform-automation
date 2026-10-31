"""Snowpark session creation.

Two ways to get a session:
1. Inside Snowflake (Streamlit in Snowflake, stored procedures): the platform
   hands us an already-authenticated session via get_active_session().
2. In the Snowflake sandbox / CI: an OAuth token file is provided through
   SNOWFLAKE_TOKEN_FILE_PATH. We read it at connect time and never store it.

No passwords or keys are ever written in this code.
"""
from __future__ import annotations

import os

from snowflake.snowpark import Session

from core.config import settings


def _token_from_file() -> str:
    path = os.environ.get("SNOWFLAKE_TOKEN_FILE_PATH", "/snowflake/session/token")
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def get_session(role: str = "OPSAGENTX_ROLE") -> Session:
    """Return a Snowpark session, preferring the platform-provided one."""
    try:
        from snowflake.snowpark.context import get_active_session

        return get_active_session()
    except Exception:  # noqa: BLE001 - no active session means we are outside Snowflake
        pass

    session = Session.builder.configs(
        {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "host": os.environ["SNOWFLAKE_HOST"],
            "authenticator": "oauth",
            "token": _token_from_file(),
            "role": role,
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            "database": settings.database,
            "schema": settings.schema,
        }
    ).create()
    return session
