"""Headless UI smoke test: render every page with Streamlit AppTest against live Snowflake.

Not part of the default pytest run (needs a live session). Read-only except where
noted. Usage (with the UI venv that pins streamlit==1.52.2):

    python scripts/check_stage8_ui.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import streamlit  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["dashboard", "incidents", "diagnosis", "approvals", "executions", "audit"]


def render(page: str) -> AppTest:
    at = AppTest.from_file("streamlit_app.py", default_timeout=180)
    at.run()
    if page != "dashboard":
        at.switch_page(f"app_pages/{page}.py")
        at.run()
    return at


def main() -> int:
    print("streamlit", streamlit.__version__)
    failures = 0
    for page in PAGES:
        at = render(page)
        errors = [e.value for e in at.exception]
        texts = [m.value for m in at.markdown][:2]
        status = "FAIL" if errors else "OK  "
        failures += bool(errors)
        print(f"{status} {page:11} titles={[t.value for t in at.title]} metrics={len(at.metric)} "
              f"dataframes={len(at.dataframe)} buttons={len(at.button)} errors={len(at.error)}")
        for error in errors:
            print("      EXCEPTION:", str(error)[:600])
        for err in at.error:
            print("      st.error:", str(err.value)[:200])
        _ = texts
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
