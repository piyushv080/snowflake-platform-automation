"""Stage 5 tests: retrieval behaviour without calling Snowflake."""
from services.cortex_search_retriever import CortexSearchRetriever, FallbackRunbookRetriever
from services.knowledge_service import KeywordRunbookRetriever, RunbookHit, RunbookRetriever


class BrokenRetriever(RunbookRetriever):
    name = "cortex_search"

    def search(self, query, category=None, limit=3):
        raise RuntimeError("service unavailable")


def test_keyword_retriever_ranks_category_first_and_skips_drafts(repo):
    hits = KeywordRunbookRetriever(repo).search("disk usage tmp growth", category="disk_space")
    assert hits[0].runbook_id == "RB-001"
    assert all(h.runbook_id != "RB-900" for h in hits)


def test_fallback_is_used_and_reported_when_primary_fails(repo):
    retriever = FallbackRunbookRetriever(BrokenRetriever(), KeywordRunbookRetriever(repo))
    hits = retriever.search("disk full", category="disk_space")
    assert hits and hits[0].runbook_id == "RB-001"
    assert retriever.name == "keyword_fallback (primary failed)"
    assert "unavailable" in retriever.last_error


def test_reranker_margin_drops_unrelated_hits():
    retriever = CortexSearchRetriever.__new__(CortexSearchRetriever)  # no session needed
    results = [
        {"RUNBOOK_ID": "RB-001", "TITLE": "Disk", "CATEGORY": "disk_space", "CONTENT": "x",
         "@scores": {"reranker_score": -5.9}},
        {"RUNBOOK_ID": "RB-003", "TITLE": "Memory", "CATEGORY": "memory", "CONTENT": "y",
         "@scores": {"reranker_score": -11.2}},
    ]
    hits = retriever._to_hits(results)
    assert [h.runbook_id for h in hits] == ["RB-001"]
    assert isinstance(hits[0], RunbookHit) and hits[0].retriever == "cortex_search"
