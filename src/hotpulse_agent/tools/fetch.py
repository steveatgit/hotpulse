from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

from ..config import AppConfig
from ..schemas import SearchDocument
from .base import BaseTool


class FetchPageTool(BaseTool):
    name = "fetch_page"

    def __init__(self, config: AppConfig | None = None) -> None:
        self.provider = (
            config.fetch.provider if config is not None else os.getenv("HOTPULSE_FETCH_PROVIDER", "local")
        ).strip().lower()
        self.firecrawl_api_key = (
            config.fetch.firecrawl_api_key if config is not None else os.getenv("FIRECRAWL_API_KEY", "")
        )

    def run(self, docs: list[SearchDocument], fetched_doc_ids: set[str]) -> dict | None:
        for doc in docs:
            if doc.doc_id not in fetched_doc_ids:
                return self._enrich(doc)
        return None

    def _enrich(self, doc: SearchDocument) -> dict:
        if self.provider == "local":
            return {"doc": doc, "fetch_mode": "local"}
        if self.provider == "firecrawl":
            try:
                return {"doc": self._firecrawl_fetch(doc), "fetch_mode": "firecrawl"}
            except Exception:
                try:
                    return {"doc": self._http_fetch(doc), "fetch_mode": "http-fallback"}
                except Exception:
                    return {"doc": doc, "fetch_mode": "original-doc-fallback"}
        if self.provider == "http":
            try:
                return {"doc": self._http_fetch(doc), "fetch_mode": "http"}
            except Exception:
                return {"doc": doc, "fetch_mode": "original-doc-fallback"}
        raise ValueError(f"Unsupported HOTPULSE_FETCH_PROVIDER: {self.provider}")

    def _firecrawl_fetch(self, doc: SearchDocument) -> SearchDocument:
        if not self.firecrawl_api_key:
            raise ValueError("FIRECRAWL_API_KEY must be set when HOTPULSE_FETCH_PROVIDER=firecrawl")
        payload = json.dumps(
            {
                "url": doc.url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.firecrawl.dev/v2/scrape",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.firecrawl_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = body.get("data", {})
        content = data.get("markdown") or data.get("html") or doc.content
        metadata = data.get("metadata", {})
        return SearchDocument(
            doc_id=doc.doc_id,
            event_id=doc.event_id,
            title=metadata.get("title") or doc.title,
            source=doc.source,
            source_type=doc.source_type,
            published_at=doc.published_at,
            reliability=doc.reliability,
            url=doc.url,
            content=content,
            claims=doc.claims,
            entities=doc.entities,
            tags=doc.tags,
        )

    def _http_fetch(self, doc: SearchDocument) -> SearchDocument:
        request = urllib.request.Request(
            doc.url,
            headers={"User-Agent": "HotPulseAgent/0.1"},
            method="GET",
        )
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            body = response.read(100000).decode("utf-8", errors="ignore")
        return SearchDocument(
            doc_id=doc.doc_id,
            event_id=doc.event_id,
            title=doc.title,
            source=doc.source,
            source_type=doc.source_type,
            published_at=doc.published_at,
            reliability=doc.reliability,
            url=doc.url,
            content=body,
            claims=doc.claims,
            entities=doc.entities,
            tags=doc.tags,
        )