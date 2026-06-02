from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
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


def _hostname(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc or "unknown"


def _published_at(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    return "1970-01-01T00:00:00Z"


def _extract_domain(tags: list[str]) -> str:
    if not tags:
        return "web"
    return tags[0]


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

    def __init__(self, api_key: str, search_depth: str = "advanced", topic: str = "news") -> None:
        self.api_key = api_key
        self.search_depth = search_depth
        self.topic = topic

    def run(self, query: str, event_id: str | None = None, top_k: int = 5) -> list[SearchDocument]:
        normalized_query = _normalize_tavily_query(query)
        payload = json.dumps(
            {
                "query": normalized_query,
                "topic": self.topic,
                "search_depth": self.search_depth,
                "max_results": top_k,
                "include_answer": False,
                "include_raw_content": False,
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
            documents.append(
                SearchDocument(
                    doc_id=f"tavily-{index}-{_safe_slug(title)}",
                    event_id=event_id,
                    title=title,
                    source=_hostname(url),
                    source_type="web",
                    published_at=_published_at(item.get("published_date")),
                    reliability=0.75,
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
            documents.append(
                SearchDocument(
                    doc_id=f"serpapi-{index}-{_safe_slug(title)}",
                    event_id=event_id,
                    title=title,
                    source=_hostname(result_url),
                    source_type="web",
                    published_at=_published_at(item.get("date")),
                    reliability=0.72,
                    url=result_url,
                    content=item.get("snippet", "") or "",
                    claims=[],
                    entities=[],
                    tags=["serpapi"],
                )
            )
        return documents


def build_search_tool(corpus_path: Path, config: AppConfig | None = None) -> BaseTool:
    provider = (
        config.search.provider if config is not None else os.getenv("HOTPULSE_SEARCH_PROVIDER", "local")
    ).strip().lower()
    if provider == "tavily":
        api_key = config.search.tavily_api_key if config is not None else os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            raise ValueError("TAVILY_API_KEY must be set when HOTPULSE_SEARCH_PROVIDER=tavily")
        return TavilySearchTool(
            api_key=api_key,
            search_depth=(
                config.search.tavily_search_depth
                if config is not None
                else os.getenv("HOTPULSE_TAVILY_SEARCH_DEPTH", "advanced")
            ),
            topic=config.search.tavily_topic if config is not None else os.getenv("HOTPULSE_TAVILY_TOPIC", "news"),
        )
    if provider == "serpapi":
        api_key = config.search.serpapi_api_key if config is not None else os.getenv("SERPAPI_API_KEY", "")
        if not api_key:
            raise ValueError("SERPAPI_API_KEY must be set when HOTPULSE_SEARCH_PROVIDER=serpapi")
        return SerpApiSearchTool(
            api_key=api_key,
            engine=(
                config.search.serpapi_engine
                if config is not None
                else os.getenv("HOTPULSE_SERPAPI_ENGINE", "google")
            ),
            gl=config.search.serpapi_gl if config is not None else os.getenv("HOTPULSE_SERPAPI_GL", "us"),
            hl=config.search.serpapi_hl if config is not None else os.getenv("HOTPULSE_SERPAPI_HL", "en"),
        )
    return LocalSearchTool(corpus_path)