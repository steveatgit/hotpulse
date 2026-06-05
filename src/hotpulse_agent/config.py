from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    max_tokens: int = 1024
    planner_mode: str = ""
    router_mode: str = ""
    reflector_mode: str = ""
    reporter_mode: str = ""
    planner_timeout: float = 0.0
    router_timeout: float = 0.0
    reflector_timeout: float = 0.0
    reporter_timeout: float = 0.0
    planner_max_tokens: int = 0
    router_max_tokens: int = 0
    reflector_max_tokens: int = 0
    reporter_max_tokens: int = 0
    circuit_breaker_threshold: int = 2
    consecutive_timeouts: int = 0
    circuit_open_reason: str = ""
    call_history: list[dict] = field(default_factory=list)

    def mode_for(self, component: str) -> str:
        if self.mode.strip().lower() == "rule":
            return "rule"
        value = getattr(self, f"{component}_mode", "")
        return (value or self.mode).strip().lower()

    def timeout_for(self, component: str) -> float:
        value = float(getattr(self, f"{component}_timeout", 0.0) or 0.0)
        return value if value > 0 else self.timeout

    def max_tokens_for(self, component: str) -> int:
        value = int(getattr(self, f"{component}_max_tokens", 0) or 0)
        return value if value > 0 else self.max_tokens


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
            max_tokens=int(_pick(payload, ["policy", "max_tokens"], os.getenv("HOTPULSE_LLM_MAX_TOKENS", "1024"))),
            planner_mode=_component_pick(payload, "planner", "mode", ""),
            router_mode=_component_pick(payload, "router", "mode", ""),
            reflector_mode=_component_pick(payload, "reflector", "mode", ""),
            reporter_mode=_component_pick(payload, "reporter", "mode", ""),
            planner_timeout=float(_component_pick(payload, "planner", "timeout", "0")),
            router_timeout=float(_component_pick(payload, "router", "timeout", "0")),
            reflector_timeout=float(_component_pick(payload, "reflector", "timeout", "0")),
            reporter_timeout=float(_component_pick(payload, "reporter", "timeout", "0")),
            planner_max_tokens=int(_component_pick(payload, "planner", "max_tokens", "0")),
            router_max_tokens=int(_component_pick(payload, "router", "max_tokens", "0")),
            reflector_max_tokens=int(_component_pick(payload, "reflector", "max_tokens", "0")),
            reporter_max_tokens=int(_component_pick(payload, "reporter", "max_tokens", "0")),
            circuit_breaker_threshold=int(
                _pick(payload, ["policy", "circuit_breaker_threshold"], os.getenv("HOTPULSE_LLM_CIRCUIT_BREAKER_THRESHOLD", "2"))
            ),
        ),
    )


def override_config(
    config: AppConfig,
    *,
    search_provider: str | None = None,
    fetch_provider: str | None = None,
    policy_mode: str | None = None,
) -> AppConfig:
    return AppConfig(
        search=replace(config.search, provider=(search_provider or config.search.provider).strip()),
        fetch=replace(config.fetch, provider=(fetch_provider or config.fetch.provider).strip()),
        policy=replace(config.policy, mode=(policy_mode or config.policy.mode).strip().lower()),
    )


def _load_file_payload(base_dir: Path, config_path: Path | None) -> dict[str, Any]:
    target = config_path or (base_dir / "hotpulse.config.json")
    if not target.exists():
        if config_path is not None:
            raise FileNotFoundError(f"Config file not found: {target}")
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


def _component_pick(payload: dict[str, Any], component: str, key: str, fallback: str) -> str:
    return _pick(payload, ["policy", "components", component, key], fallback)
