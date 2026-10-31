"""Runbook retrieval (knowledge layer).

`RunbookRetriever` is the interface. Stage 4 ships the keyword fallback;
Stage 5 adds `CortexSearchRetriever`, which does hybrid semantic + keyword
search inside Snowflake.

Only APPROVED runbooks are ever returned. Draft runbooks (e.g. RB-900
'Aggressive disk purge') can never influence a recommendation.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from database.repositories import KnowledgeRepository


@dataclass
class RunbookHit:
    runbook_id: str
    title: str
    category: str
    content: str
    score: float
    retriever: str          # which implementation produced it


class RunbookRetriever(ABC):
    name = "abstract"

    @abstractmethod
    def search(self, query: str, category: Optional[str] = None, limit: int = 3) -> list[RunbookHit]: ...


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "on", "of", "to", "and", "is", "in", "for", "at", "with", "has", "server"}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


class KeywordRunbookRetriever(RunbookRetriever):
    """Fallback: simple word-overlap scoring plus a category boost.

    Limitations vs Cortex Search: no synonyms ("disk full" won't match
    "filesystem utilization"), no semantic similarity, scores are crude.
    """

    name = "keyword_fallback"

    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def search(self, query: str, category: Optional[str] = None, limit: int = 3) -> list[RunbookHit]:
        query_tokens = _tokens(query)
        hits = []
        for runbook in self.repository.list_runbooks(approved_only=True):
            document_tokens = _tokens(f"{runbook.title} {runbook.description or ''} {runbook.content}")
            overlap = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            score = overlap + (1.0 if category and runbook.category == category else 0.0)
            if score > 0:
                hits.append(RunbookHit(runbook.runbook_id, runbook.title, runbook.category,
                                       runbook.content, round(score, 3), self.name))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:limit]
