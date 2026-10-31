"""Live Stage 5 check: compare keyword fallback vs Cortex Search on the same queries.

Read-only. Usage:  python scripts/check_stage5_search.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.connection import get_session  # noqa: E402
from database.snowflake_repository import SnowflakeRepository  # noqa: E402
from services.cortex_search_retriever import CortexSearchRetriever  # noqa: E402
from services.knowledge_service import KeywordRunbookRetriever  # noqa: E402

QUERIES = [
    # (query, category or None, expected runbook)
    ("disk space Disk utilization reached 95% on / of server-01.", "disk_space", "RB-001"),
    ("filesystem almost full, scratch folder ballooning", None, "RB-001"),
    ("box is pegged, processor saturated after deploy", None, "RB-002"),
    ("web server daemon died and health probe refused connection", None, "RB-004"),
    ("automation job could not reach hosts over ssh", None, "RB-005"),
    ("kernel killed the process, ran out of RAM", None, "RB-003"),
]


def main() -> None:
    session = get_session()
    keyword = KeywordRunbookRetriever(SnowflakeRepository(session))
    cortex = CortexSearchRetriever(session)

    totals = {"keyword": 0, "cortex": 0}
    for query, category, expected in QUERIES:
        kw_hits = keyword.search(query, category)
        started = time.time()
        cx_hits = cortex.search(query, category)
        elapsed = time.time() - started
        kw_top = kw_hits[0].runbook_id if kw_hits else "-"
        cx_top = cx_hits[0].runbook_id if cx_hits else "-"
        totals["keyword"] += kw_top == expected
        totals["cortex"] += cx_top == expected
        print(f"{query[:55]:55} | expect {expected} | keyword {kw_top:6} ({len(kw_hits)} hits)"
              f" | cortex {cx_top:6} ({len(cx_hits)} hits, {elapsed:.2f}s via {cortex.last_api})")
    print(f"\nTop-1 accuracy: keyword {totals['keyword']}/{len(QUERIES)}, cortex {totals['cortex']}/{len(QUERIES)}")
    session.close()


if __name__ == "__main__":
    main()
