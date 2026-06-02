from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any


@dataclass
class SearchConfig:
    provider: str = "local"
    tavily_api_key: str = ""
    tavily_search_depth: str = "advanced"
    tavily_topic: str = "news"
    serpapi_api_key: str = ""
    serpapi_engine: str = "google"
    serpapi_gl: str = "us"
    serpapi_hl: str = "en"


@dataclass
class FetchConfig:
    provider: str = "local"
    firecrawl_api_key: str = ""


@dataclass
class PolicyConfig:
    mode: str = "rule"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 20.0
    temperature: float = 0.1


@dataclass
class AppConfig:
    search: SearchConfig
    fetch: FetchConfig
    policy: PolicyConfig


def load_config(base_dir: Path, config_path: Path | None = None) -> AppConfig:
    payload = _load_file_payload(base_dir=base_dir, config_path=config_path)
    return AppConfig(
        search=SearchConfig(
            provider=_pick(payload, ["search", "provider"], os.getenv("HOTPULSE_SEARCH_PROVIDER", "local")),
            tavily_api_key=_pick(payload, ["search", "tavily_api_key"], os.getenv("TAVILY_API_KEY", "")),
            tavily_search_depth=_pick(
                payload,
                ["search", "tavily_search_depth"],
                os.getenv("HOTPULSE_TAVILY_SEARCH_DEPTH", "advanced"),
            ),
            tavily_topic=_pick(payload, ["search", "tavily_topic"], os.getenv("HOTPULSE_TAVILY_TOPIC", "news")),
            serpapi_api_key=_pick(payload, ["search", "serpapi_api_key"], os.getenv("SERPAPI_API_KEY", "")),
            serpapi_engine=_pick(payload, ["search", "serpapi_engine"], os.getenv("HOTPULSE_SERPAPI_ENGINE", "google")),
            serpapi_gl=_pick(payload, ["search", "serpapi_gl"], os.getenv("HOTPULSE_SERPAPI_GL", "us")),
            serpapi_hl=_pick(payload, ["search", "serpapi_hl"], os.getenv("HOTPULSE_SERPAPI_HL", "en")),
        ),
        fetch=FetchConfig(
            provider=_pick(payload, ["fetch", "provider"], os.getenv("HOTPULSE_FETCH_PROVIDER", "local")),
            firecrawl_api_key=_pick(payload, ["fetch", "firecrawl_api_key"], os.getenv("FIRECRAWL_API_KEY", "")),
        ),
        policy=PolicyConfig(
            mode=_pick(payload, ["policy", "mode"], os.getenv("HOTPULSE_POLICY_MODE", "rule")).lower(),
            base_url=_pick(payload, ["policy", "base_url"], _env_pick("HOTPULSE_LLM_BASE_URL", "OPENAI_BASE_URL")),
            api_key=_pick(payload, ["policy", "api_key"], _env_pick("HOTPULSE_LLM_API_KEY", "OPENAI_API_KEY")),
            model=_pick(payload, ["policy", "model"], _env_pick("HOTPULSE_LLM_MODEL", "OPENAI_MODEL")),
            timeout=float(_pick(payload, ["policy", "timeout"], os.getenv("HOTPULSE_LLM_TIMEOUT", "20"))),
            temperature=float(
                _pick(payload, ["policy", "temperature"], os.getenv("HOTPULSE_LLM_TEMPERATURE", "0.1"))
            ),
        ),
    )


def override_config(
    config: AppConfig,
    *,
    search_provider: str | None = None,
    fetch_provider: str | None = None,
) -> AppConfig:
    return AppConfig(
        search=replace(config.search, provider=(search_provider or config.search.provider).strip()),
        fetch=replace(config.fetch, provider=(fetch_provider or config.fetch.provider).strip()),
        policy=config.policy,
    )


def _load_file_payload(base_dir: Path, config_path: Path | None) -> dict[str, Any]:
    target = config_path or (base_dir / "hotpulse.config.json")
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Config file must contain a JSON object: {target}")
    return payload


def _pick(payload: dict[str, Any], path: list[str], fallback: str) -> str:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return fallback
        current = current[key]
    if current is None:
        return fallback
    value = str(current).strip()
    if not value:
        return fallback
    return value


def _env_pick(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""