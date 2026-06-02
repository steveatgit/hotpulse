from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotpulse_agent.cli import build_orchestrator  # noqa: E402
from hotpulse_agent.config import AppConfig, FetchConfig, PolicyConfig, SearchConfig, load_config  # noqa: E402


def build_runtime_config(base_config: AppConfig, policy_mode: str, mode: str) -> AppConfig:
    search_provider = "local" if mode == "offline" else base_config.search.provider
    fetch_provider = "local" if mode == "offline" else base_config.fetch.provider
    return AppConfig(
        search=SearchConfig(
            provider=search_provider,
            tavily_api_key=base_config.search.tavily_api_key,
            tavily_search_depth=base_config.search.tavily_search_depth,
            tavily_topic=base_config.search.tavily_topic,
            serpapi_api_key=base_config.search.serpapi_api_key,
            serpapi_engine=base_config.search.serpapi_engine,
            serpapi_gl=base_config.search.serpapi_gl,
            serpapi_hl=base_config.search.serpapi_hl,
        ),
        fetch=FetchConfig(
            provider=fetch_provider,
            firecrawl_api_key=base_config.fetch.firecrawl_api_key,
        ),
        policy=PolicyConfig(
            mode=policy_mode,
            base_url=base_config.policy.base_url,
            api_key=base_config.policy.api_key,
            model=base_config.policy.model,
            timeout=base_config.policy.timeout,
            temperature=base_config.policy.temperature,
        ),
    )


def summarize_result(case_name: str, policy_mode: str, result) -> dict:
    planning_sources = [item.get("decision_source", "unknown") for item in result.metrics.get("planning_decisions", [])]
    reflection_sources = [
        item.get("decision_source", "unknown") for item in result.metrics.get("reflection_decisions", [])
    ]
    fallback_flags = {
        "plan_fallback": any(source.endswith("fallback") for source in planning_sources),
        "router_fallback": any(source.endswith("fallback") for source in result.metrics.get("router_decision_sources", [])),
        "reflector_fallback": any(source.endswith("fallback") for source in reflection_sources),
    }
    return {
        "case": case_name,
        "policy_mode": policy_mode,
        "state": result.state.value,
        "evidence_count": result.metrics.get("evidence_count", 0),
        "source_diversity": result.metrics.get("source_diversity", 0),
        "coverage": round(result.metrics.get("coverage", 0.0), 3),
        "plan_source": result.metrics.get("plan_metadata", {}).get("decision_source", "unknown"),
        "router_sources": result.metrics.get("router_decision_sources", []),
        "reflector_sources": reflection_sources,
        "query_rewrites": max(len(result.metrics.get("query_history", [])) - 1, 0),
        **fallback_flags,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HotPulse evals with A/B policy comparison.")
    parser.add_argument(
        "--policy-modes",
        default="rule,hybrid,llm",
        help="Comma-separated policy modes to compare. Example: rule,hybrid,llm",
    )
    parser.add_argument(
        "--mode",
        default="offline",
        choices=["offline", "online"],
        help="Whether to force local providers or reuse configured online providers.",
    )
    parser.add_argument(
        "--config",
        default="hotpulse.config.json",
        help="Path to the project config JSON file.",
    )
    args = parser.parse_args()

    policy_modes = [item.strip() for item in args.policy_modes.split(",") if item.strip()]
    config_path = Path(args.config).expanduser().resolve()
    base_config = load_config(ROOT, config_path=config_path)
    case_dir = ROOT / "examples" / "cases"

    rows = []
    for case_path in sorted(case_dir.glob("*.json")):
        with case_path.open("r", encoding="utf-8") as handle:
            case = json.load(handle)
        for policy_mode in policy_modes:
            runtime_config = build_runtime_config(base_config, policy_mode=policy_mode, mode=args.mode)
            orchestrator = build_orchestrator(ROOT, config=runtime_config)
            result = orchestrator.run(question=case["question"], event_id=case.get("event_id"))
            rows.append(summarize_result(case_path.name, policy_mode, result))

    print(
        "case,policy_mode,state,evidence_count,source_diversity,coverage,plan_source,"
        "router_sources,reflector_sources,query_rewrites,plan_fallback,router_fallback,reflector_fallback"
    )
    for row in rows:
        print(
            f"{row['case']},{row['policy_mode']},{row['state']},{row['evidence_count']},"
            f"{row['source_diversity']},{row['coverage']},{row['plan_source']},"
            f"\"{'|'.join(row['router_sources'])}\",\"{'|'.join(row['reflector_sources'])}\",{row['query_rewrites']},"
            f"{row['plan_fallback']},{row['router_fallback']},{row['reflector_fallback']}"
        )


if __name__ == "__main__":
    main()