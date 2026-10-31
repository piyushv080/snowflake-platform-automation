"""Deploy the OpsAgentX Streamlit app: upload source files to a stage, then CREATE STREAMLIT.

Only runtime files are uploaded (no tests, scripts, or docs). Usage:

    python scripts/deploy_streamlit.py
"""
import glob
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from run_sql import connect  # noqa: E402

STAGE = "@OPSAGENTX_DB.OPSAGENTX_SCHEMA.APP_STAGE/opsagentx"
PACKAGES = ["core", "database", "llm", "agent", "integrations", "services", "ui", "app_pages"]
TOP_LEVEL = ["streamlit_app.py", "environment.yml"]


def app_files() -> list[str]:
    files = [os.path.join(ROOT, f) for f in TOP_LEVEL]
    files.append(os.path.join(ROOT, ".streamlit", "config.toml"))
    for package in PACKAGES:
        files += sorted(glob.glob(os.path.join(ROOT, package, "*.py")))
    return files


def main() -> None:
    connection = connect("OPSAGENTX_ROLE")
    cursor = connection.cursor()
    try:
        cursor.execute("USE SCHEMA OPSAGENTX_DB.OPSAGENTX_SCHEMA")
        cursor.execute("CREATE STAGE IF NOT EXISTS APP_STAGE DIRECTORY = (ENABLE = TRUE) "
                       "COMMENT = 'OpsAgentX Streamlit app source files'")
        cursor.execute(f"REMOVE {STAGE}/")
        for path in app_files():
            relative_dir = os.path.dirname(os.path.relpath(path, ROOT))
            target = f"{STAGE}/{relative_dir}".rstrip("/").replace("\\", "/")
            cursor.execute(f"PUT 'file://{path}' '{target}' AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
            row = cursor.fetchone()
            print(f"PUT {os.path.relpath(path, ROOT):40} -> {target.split('/opsagentx')[-1] or '/'} [{row[6]}]")

        with open(os.path.join(ROOT, "sql", "05_streamlit.sql"), encoding="utf-8") as handle:
            sql_text = handle.read()
        create_streamlit = sql_text[sql_text.index("CREATE OR REPLACE STREAMLIT"):].strip().rstrip(";")
        cursor.execute(create_streamlit)
        print(cursor.fetchone()[0])
    finally:
        connection.close()


if __name__ == "__main__":
    main()
