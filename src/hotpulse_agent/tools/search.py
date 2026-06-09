from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..schemas import SearchDocument
from .base import BaseTool


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))


def _safe_slug(text: str) -> str:
    parts = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return "-".join(parts[:8]) or "document"


def _stable_doc_id(provider: str, index: int, title: str, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8] if url else f"{index:04d}"
    return f"{provider}-{digest}-{_safe_slug(title)}"


def _hostname(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc or "unknown"


def _published_at(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return raw
    return "1970-01-01T00:00:00Z"


def _source_quality(url: str) -> tuple[str, float]:
    host = _hostname(url).lower()
    if not host:
        return "web", 0.65
    official_markers = ["openai.com", "sec.gov", ".gov", ".edu"]
    if any(marker in host for marker in official_markers):
        return "official", 0.9
    high_quality_media = [
        "reuters.com",
        "apnews.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "nytimes.com",
        "theverge.com",
        "businessinsider.com",
    ]
    if any(marker in host for marker in high_quality_media):
        return "media", 0.8
    return "web", 0.68


def _normalize_tavily_query(query: str, max_chars: int = 380, max_terms: int = 28) -> str:
    compact = re.sub(r"\s+", " ", query).strip()
    if len(compact) <= max_chars:
        return compact

    phrases = re.findall(r'"([^"]+)"', compact)
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", compact)
    stopwords = {
        "and",
        "or",
        "the",
        "in",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "latest",
        "developments",
        "evidence",
        "disputed",
        "claims",
    }

    ordered_terms: list[str] = []
    seen: set[str] = set()
    for item in phrases + terms:
        token = item.strip()
        if not token:
            continue
        key = token.lower()
        if key in stopwords or key in seen:
            continue
        seen.add(key)
        ordered_terms.append(token)
        if len(ordered_terms) >= max_terms:
            break

    fallback = " ".join(ordered_terms).strip() or compact[:max_chars].strip()
    return fallback[:max_chars].strip()


class LocalSearchTool(BaseTool):
    name = "search_web"

    def __init__(self, corpus_path: Path) -> None:
        with corpus_path.open("r", encoding="utf-8") as handle:
            rows = json.load(handle)
        self.documents = [SearchDocument(**row) for row in rows]

    def run(self, query: str, event_id: str | None = None, top_k: int = 5) -> list[SearchDocument]:
        query_tokens = _tokenize(query)
        scored = []
        for doc in self.documents:
            if event_id and doc.event_id != event_id:
                continue
            bag = _tokenize(" ".join([doc.title, doc.content, " ".join(doc.tags), " ".join(doc.entities)]))
            overlap = len(query_tokens & bag)
            score = overlap + doc.reliability
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], item[1].published_at), reverse=False)
        return [doc for _, doc in scored[:top_k]]

class TavilySearchTool(BaseTool):
    name = "search_web"

    def __init__(
        self,
        api_key: str,
        search_depth: str = "advanced",
        topic: str = "news",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.search_depth = search_depth
        self.topic = topic
        self.include_domains = include_domains or []
        self.exclude_domains = exclude_domains or []

    def run(self, query: str, event_id: str | None = None, top_k: int = 5) -> list[SearchDocument]:
        normalized_query = _normalize_tavily_query(query)
        payload = json.dumps(
            {
                "query": normalized_query,
                "topic": self.topic,
                "search_depth": self.search_depth,
                "max_results": top_k,
                "include_answer": False,
                "include_raw_content": True,
                "include_domains": self.include_domains,
                "exclude_domains": self.exclude_domains,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))

        documents = []
        for index, item in enumerate(body.get("results", [])[:top_k], start=1):
            url = item.get("url", "")
            content = item.get("content", "") or item.get("raw_content", "") or ""
            title = item.get("title", f"Tavily result {index}")
            source_type, reliability = _source_quality(url)
            documents.append(
                SearchDocument(
                    doc_id=_stable_doc_id("tavily", index, title, url),
                    event_id=event_id,
                    title=title,
                    source=_hostname(url),
                    source_type=source_type,
                    published_at=_published_at(item.get("published_date")),
                    reliability=reliability,
                    url=url,
                    content=content,
                    claims=[],
                    entities=[],
                    tags=["tavily"],
                )
            )
        return documents


class SerpApiSearchTool(BaseTool):
    name = "search_web"

    def __init__(self, api_key: str, engine: str = "google", gl: str = "us", hl: str = "en") -> None:
        self.api_key = api_key
        self.engine = engine
        self.gl = gl
        self.hl = hl

    def run(self, query: str, event_id: str | None = None, top_k: int = 5) -> list[SearchDocument]:
        params = urllib.parse.urlencode(
            {
                "engine": self.engine,
                "q": query,
                "api_key": self.api_key,
                "gl": self.gl,
                "hl": self.hl,
                "num": top_k,
            }
        )
        url = f"https://serpapi.com/search?{params}"
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))

        documents = []
        for index, item in enumerate(body.get("organic_results", [])[:top_k], start=1):
            result_url = item.get("link", "")
            title = item.get("title", f"SerpApi result {index}")
            source_type, reliability = _source_quality(result_url)
            documents.append(
                SearchDocument(
                    doc_id=_stable_doc_id("serpapi", index, title, result_url),
                    event_id=event_id,
                    title=title,
                    source=_hostname(result_url),
                    source_type=source_type,
                    published_at=_published_at(item.get("date")),
                    reliability=reliability,
                    url=result_url,
                    content=item.get("snippet", "") or "",
                    claims=[],
                    entities=[],
                    tags=["serpapi"],
                )
            )
        return documents


class FallbackSearchTool(BaseTool):
    name = "search_web"

    def __init__(self, tools: list[BaseTool], min_results: int = 3, min_sources: int = 2) -> None:
        if not tools:
            raise ValueError("At least one search provider is required.")
        self.tools = tools
        self.min_results = min_results
        self.min_sources = min_sources

    def run(self, query: str, event_id: str | None = None, top_k: int = 5) -> list[SearchDocument]:
        documents: list[SearchDocument] = []
        seen_urls: set[str] = set()
        last_error: Exception | None = None
        for tool in self.tools:
            try:
                for doc in tool.run(query=query, event_id=event_id, top_k=top_k):
                    key = (doc.url or doc.doc_id).strip().lower()
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    documents.append(doc)
                source_count = len({doc.source for doc in documents if doc.source})
                if len(documents) >= min(top_k, self.min_results) and source_count >= self.min_sources:
                    break
            except Exception as exc:
                last_error = exc
                continue
        if documents:
            return _diversify_documents(documents, top_k=top_k)
        if last_error is not None:
            raise last_error
        return []


def _diversify_documents(documents: list[SearchDocument], top_k: int) -> list[SearchDocument]:
    grouped: dict[str, list[SearchDocument]] = {}
    source_order: list[str] = []
    for doc in documents:
        source = doc.source or doc.doc_id
        if source not in grouped:
            grouped[source] = []
            source_order.append(source)
        grouped[source].append(doc)

    selected: list[SearchDocument] = []
    while len(selected) < top_k:
        added = False
        for source in source_order:
            if grouped[source]:
                selected.append(grouped[source].pop(0))
                added = True
                if len(selected) >= top_k:
                    break
        if not added:
            break
    return selected


def build_search_tool(corpus_path: Path, config: AppConfig | None = None) -> BaseTool:
    provider = (
        config.search.provider if config is not None else os.getenv("HOTPULSE_SEARCH_PROVIDER", "local")
    ).strip().lower()
    if provider == "tavily":
        api_key = config.search.tavily_api_key if config is not None else os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            raise ValueError("TAVILY_API_KEY must be set when HOTPULSE_SEARCH_PROVIDER=tavily")
        tools: list[BaseTool] = [
            TavilySearchTool(
                api_key=api_key,
                search_depth=(
                    config.search.tavily_search_depth
                    if config is not None
                    else os.getenv("HOTPULSE_TAVILY_SEARCH_DEPTH", "advanced")
                ),
                topic=config.search.tavily_topic if config is not None else os.getenv("HOTPULSE_TAVILY_TOPIC", "news"),
                include_domains=config.search.tavily_include_domains if config is not None else [],
                exclude_domains=config.search.tavily_exclude_domains if config is not None else [],
            )
        ]
        serpapi_key = config.search.serpapi_api_key if config is not None else os.getenv("SERPAPI_API_KEY", "")
        if serpapi_key:
            tools.append(
                SerpApiSearchTool(
                    api_key=serpapi_key,
                    engine=config.search.serpapi_engine if config is not None else os.getenv("HOTPULSE_SERPAPI_ENGINE", "google"),
                    gl=config.search.serpapi_gl if config is not None else os.getenv("HOTPULSE_SERPAPI_GL", "us"),
                    hl=config.search.serpapi_hl if config is not None else os.getenv("HOTPULSE_SERPAPI_HL", "en"),
                )
            )
        return FallbackSearchTool(tools)
    if provider == "serpapi":
        api_key = config.search.serpapi_api_key if config is not None else os.getenv("SERPAPI_API_KEY", "")
        if not api_key:
            raise ValueError("SERPAPI_API_KEY must be set when HOTPULSE_SEARCH_PROVIDER=serpapi")
        tools = [
            SerpApiSearchTool(
                api_key=api_key,
                engine=config.search.serpapi_engine if config is not None else os.getenv("HOTPULSE_SERPAPI_ENGINE", "google"),
                gl=config.search.serpapi_gl if config is not None else os.getenv("HOTPULSE_SERPAPI_GL", "us"),
                hl=config.search.serpapi_hl if config is not None else os.getenv("HOTPULSE_SERPAPI_HL", "en"),
            )
        ]
        tavily_key = config.search.tavily_api_key if config is not None else os.getenv("TAVILY_API_KEY", "")
        if tavily_key:
            tools.append(
                TavilySearchTool(
                    api_key=tavily_key,
                    search_depth=(
                        config.search.tavily_search_depth
                        if config is not None
                        else os.getenv("HOTPULSE_TAVILY_SEARCH_DEPTH", "advanced")
                    ),
                    topic=config.search.tavily_topic if config is not None else os.getenv("HOTPULSE_TAVILY_TOPIC", "news"),
                    include_domains=config.search.tavily_include_domains if config is not None else [],
                    exclude_domains=config.search.tavily_exclude_domains if config is not None else [],
                )
            )
        return FallbackSearchTool(tools)
    if provider == "local":
        return LocalSearchTool(corpus_path)
    raise ValueError(f"Unsupported HOTPULSE_SEARCH_PROVIDER: {provider}")
