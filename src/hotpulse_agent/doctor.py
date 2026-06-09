from __future__ import annotations

import argparse
from pathlib import Path
import time

from .config import AppConfig, load_config, override_config
from .policy.client import LLMClient
from .tools.fetch import FetchPageTool
from .tools.search import build_search_tool


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose HotPulse online providers without printing secrets.")
    parser.add_argument("--config", help="Optional path to a HotPulse JSON config file.")
    parser.add_argument("--query", default="OpenAI latest news", help="Search query for provider smoke tests.")
    parser.add_argument("--skip-network", action="store_true", help="Only show configuration readiness.")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[2]
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    config = load_config(project_dir, config_path=config_path)

    print("== HotPulse Doctor ==")
    print(f"config: {config_path if config_path else project_dir / 'hotpulse.config.json'}")
    _print_config(config)
    if args.skip_network:
        return

    print("\n== Provider Smoke Tests ==")
    _check_search(project_dir, config, "tavily", args.query)
    _check_search(project_dir, config, "serpapi", args.query)
    _check_fetch(config, "firecrawl")
    _check_fetch(config, "jina")
    _check_llm(config)


def _print_config(config: AppConfig) -> None:
    print(f"search.provider: {config.search.provider}")
    print(f"search.tavily_api_key: {_present(config.search.tavily_api_key)}")
    print(f"search.tavily_include_domains: {config.search.tavily_include_domains or '-'}")
    print(f"search.tavily_exclude_domains: {config.search.tavily_exclude_domains or '-'}")
    print(f"search.serpapi_api_key: {_present(config.search.serpapi_api_key)}")
    print(f"fetch.provider: {config.fetch.provider}")
    print(f"fetch.firecrawl_api_key: {_present(config.fetch.firecrawl_api_key)}")
    print(f"fetch.jina_api_key: {_present(config.fetch.jina_api_key)}")
    print(f"policy.mode: {config.policy.mode}")
    print(f"policy.base_url: {_present(config.policy.base_url)}")
    print(f"policy.api_key: {_present(config.policy.api_key)}")
    print(f"policy.model: {config.policy.model or '-'}")


def _check_search(project_dir: Path, config: AppConfig, provider: str, query: str) -> None:
    test_config = override_config(config, search_provider=provider)
    started_at = time.monotonic()
    try:
        tool = build_search_tool(project_dir / "examples" / "corpus" / "documents.json", config=test_config)
        docs = tool.run(query, top_k=2)
        latency_ms = round((time.monotonic() - started_at) * 1000)
        sources = ", ".join(doc.source for doc in docs) or "-"
        print(f"{provider}: ok docs={len(docs)} latency_ms={latency_ms} sources={sources}")
    except Exception as exc:
        latency_ms = round((time.monotonic() - started_at) * 1000)
        print(f"{provider}: fail latency_ms={latency_ms} error={exc.__class__.__name__}: {exc}")


def _check_fetch(config: AppConfig, provider: str) -> None:
    test_config = override_config(config, fetch_provider=provider)
    sample_doc = _sample_doc()
    started_at = time.monotonic()
    try:
        result = FetchPageTool(config=test_config).run([sample_doc], set())
        doc = result["doc"] if result else sample_doc
        latency_ms = round((time.monotonic() - started_at) * 1000)
        mode = result.get("fetch_mode", "-") if result else "-"
        print(f"{provider}: ok mode={mode} latency_ms={latency_ms} content_len={len(doc.content)}")
    except Exception as exc:
        latency_ms = round((time.monotonic() - started_at) * 1000)
        print(f"{provider}: fail latency_ms={latency_ms} error={exc.__class__.__name__}: {exc}")


def _check_llm(config: AppConfig) -> None:
    started_at = time.monotonic()
    try:
        text = LLMClient(config.policy).chat("只输出 OK。", "请输出 OK", component="planner").strip()
        latency_ms = round((time.monotonic() - started_at) * 1000)
        print(f"llm: ok latency_ms={latency_ms} response={text[:20] or '-'}")
    except Exception as exc:
        latency_ms = round((time.monotonic() - started_at) * 1000)
        print(f"llm: fail latency_ms={latency_ms} error={exc.__class__.__name__}: {exc}")


def _sample_doc():
    from .schemas import SearchDocument

    return SearchDocument(
        doc_id="doctor-openai",
        event_id=None,
        title="OpenAI",
        source="openai.com",
        source_type="official",
        published_at="1970-01-01T00:00:00Z",
        reliability=0.9,
        url="https://openai.com/news/",
        content="",
        claims=[],
        entities=[],
        tags=["doctor"],
    )


def _present(value: str) -> str:
    return "set" if value else "missing"


if __name__ == "__main__":
    main()
