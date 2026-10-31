"""Cortex Search implementation of RunbookRetriever (Snowflake-specific).

Query path, in order:
1. snowflake.core Python API  - Snowflake's recommended low-latency client.
2. SNOWFLAKE.CORTEX.SEARCH_PREVIEW SQL function - documented as intended for
   testing/validation (higher latency, 300 kB response cap). Used only if the
   Python API is unavailable in the runtime.

`FallbackRunbookRetriever` wraps this with the keyword retriever so the app
keeps working (with lower-quality results) if the search service is down.
The retriever that actually answered is reported in RunbookHit.retriever and
written to the audit trail - we never silently pretend semantic search ran.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # only needed for type hints; keeps unit tests Snowflake-free
    from snowflake.snowpark import Session

from core.config import settings
from services.knowledge_service import RunbookHit, RunbookRetriever

logger = logging.getLogger(__name__)

_COLUMNS = ["RUNBOOK_ID", "TITLE", "CATEGORY", "CONTENT"]

# Cortex Search returns a reranker score per hit. Hits far below the best one
# are usually unrelated runbooks; we drop them so the LLM sees less noise.
RERANKER_MARGIN = 3.0


class CortexSearchRetriever(RunbookRetriever):
    name = "cortex_search"

    def __init__(self, session: Session, service_name: str = settings.search_service) -> None:
        self.session = session
        self.service_name = service_name
        self.last_api = ""      # "python_api" or "search_preview", for diagnostics
        # Set after the first Python API failure so we do not pay the failure
        # latency on every query. Reported in diagnostics, never hidden.
        self.python_api_error: Optional[str] = None

    def search(self, query: str, category: Optional[str] = None, limit: int = 3) -> list[RunbookHit]:
        request: dict[str, Any] = {"query": query, "columns": _COLUMNS, "limit": int(limit)}
        if category:
            # Category filter makes the search precise; we fall back to unfiltered below.
            request["filter"] = {"@eq": {"CATEGORY": category}}

        results = self._query(request)
        if category and not results:
            request.pop("filter")
            results = self._query(request)
        return self._to_hits(results)

    # ------------------------------------------------------------------
    def _query(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        if self.python_api_error is None:
            try:
                results = self._query_python_api(request)
                self.last_api = "python_api"
                return results
            except Exception as error:  # noqa: BLE001 - try the SQL path next
                self.python_api_error = str(error)[:300]
                logger.warning("Cortex Search Python API failed (%s); using SEARCH_PREVIEW from now on",
                               self.python_api_error)
        results = self._query_search_preview(request)
        self.last_api = "search_preview"
        return results

    def _query_python_api(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        from snowflake.core import Root  # imported lazily: optional dependency

        service = (
            Root(self.session)
            .databases[settings.database]
            .schemas[settings.schema]
            .cortex_search_services[self.service_name]
        )
        response = service.search(**request)
        return [dict(item) for item in response.results]

    def _query_search_preview(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        # SEARCH_PREVIEW only accepts constant arguments, so bind parameters are
        # used (not table columns). The JSON payload is built with json.dumps.
        rows = self.session.sql(
            "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(?, ?) AS R",
            params=[settings.fq(self.service_name), json.dumps(request)],
        ).collect()
        return json.loads(rows[0]["R"]).get("results", [])

    def _to_hits(self, results: list[dict[str, Any]]) -> list[RunbookHit]:
        if not results:
            return []

        def score_of(item: dict[str, Any]) -> float:
            scores = item.get("@scores") or {}
            return float(scores.get("reranker_score", scores.get("cosine_similarity", 0.0)))

        best = score_of(results[0])
        hits = []
        for item in results:
            score = score_of(item)
            if hits and score < best - RERANKER_MARGIN:
                break  # remaining hits are much less relevant than the top one
            hits.append(RunbookHit(
                runbook_id=item["RUNBOOK_ID"], title=item["TITLE"], category=item["CATEGORY"],
                content=item["CONTENT"], score=round(score, 3), retriever=self.name,
            ))
        return hits


class FallbackRunbookRetriever(RunbookRetriever):
    """Try the primary retriever; on error use the fallback, and say so."""

    def __init__(self, primary: RunbookRetriever, fallback: RunbookRetriever) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.last_error: Optional[str] = None

    def search(self, query: str, category: Optional[str] = None, limit: int = 3) -> list[RunbookHit]:
        try:
            self.name = self.primary.name
            self.last_error = None
            return self.primary.search(query, category, limit)
        except Exception as error:  # noqa: BLE001
            logger.warning("primary retriever %s failed: %s", self.primary.name, error)
            self.name = f"{self.fallback.name} (primary failed)"
            self.last_error = str(error)[:500]
            return self.fallback.search(query, category, limit)
