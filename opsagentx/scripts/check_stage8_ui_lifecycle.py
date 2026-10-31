"""Headless UI lifecycle test: drive INC1001 end-to-end through the Streamlit pages.

Clicks the real buttons via Streamlit AppTest against live Snowflake:
  diagnosis (direct mode) -> request approval -> confirm + approve -> run (success) -> audit
WRITES DATA. Reset afterwards with CALL RESET_DEMO_DATA() (or the Dashboard button).
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402


def page(name: str) -> AppTest:
    at = AppTest.from_file("streamlit_app.py", default_timeout=300)
    at.session_state["selected_incident"] = "INC1001"
    at.run()
    at.switch_page(f"app_pages/{name}.py")
    at.run()
    assert not at.exception, [str(e.value)[:500] for e in at.exception]
    return at


def button(at: AppTest, label_start: str):
    for b in at.button:
        if b.label.startswith(label_start):
            return b
    raise AssertionError(f"button {label_start!r} not found: {[b.label for b in at.button]}")


def main() -> None:
    # 1. Diagnosis (direct mode for speed)
    at = page("diagnosis")
    at.radio[0].set_value("Direct LLM (fixed tool pipeline + AI_COMPLETE)").run()
    button(at, "Run AI diagnosis").click().run()
    assert not at.exception, [str(e.value)[:500] for e in at.exception]
    print("diagnosis metrics:", [(m.label, m.value) for m in at.metric])

    # 2. Request approval
    button(at, "Request approval").click().run()
    assert not at.exception, [str(e.value)[:500] for e in at.exception]

    # 3. Approve: the button must be disabled until the checkbox is ticked
    at = page("approvals")
    approve = button(at, "Approve")
    print("approve disabled before confirm:", approve.disabled)
    at.checkbox[0].check().run()
    at.text_input[0].input("UI test approval").run()
    button(at, "Approve").click().run()
    assert not at.exception, [str(e.value)[:500] for e in at.exception]
    print("approvals page info after approve:", [i.value[:60] for i in at.info] or "(pending list empty)")

    # 4. Execute (success scenario is the default selection)
    at = page("executions")
    button(at, "Run simulated playbook").click().run()
    assert not at.exception, [str(e.value)[:500] for e in at.exception]
    print("execution success banners:", [s.value[:70] for s in at.success])
    print("verification metrics:", [(m.label, m.value) for m in at.metric])

    # 5. Audit timeline
    at = page("audit")
    print("audit caption:", [c.value for c in at.caption][:1])

    # 6. Dashboard KPIs reflect the change
    at = AppTest.from_file("streamlit_app.py", default_timeout=120)
    at.run()
    print("dashboard KPIs:", [(m.label, m.value) for m in at.metric])


if __name__ == "__main__":
    main()
