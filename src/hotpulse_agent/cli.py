from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AppConfig, load_config, override_config
from .orchestrator import HotPulseOrchestrator
from .planner import Planner
from .reflector import Reflector
from .reporting import ReportGenerator
from .router import ToolRouter
from .tools.extract import ExtractEvidenceTool
from .tools.fetch import FetchPageTool
from .tools.registry import ToolRegistry
from .tools.search import build_search_tool
from .tools.timeline import BuildTimelineTool


def build_orchestrator(
    base_dir: Path,
    config_path: Path | None = None,
    config: AppConfig | None = None,
) -> HotPulseOrchestrator:
    config = config or load_config(base_dir=base_dir, config_path=config_path)
    registry = ToolRegistry()
    registry.register(build_search_tool(base_dir / "examples" / "corpus" / "documents.json", config=config))
    registry.register(FetchPageTool(config=config))
    registry.register(ExtractEvidenceTool())
    registry.register(BuildTimelineTool())
    return HotPulseOrchestrator(
        planner=Planner(config.policy),
        reflector=Reflector(config.policy),
        router=ToolRouter(config.policy),
        registry=registry,
        reporter=ReportGenerator(config.policy),
    )


def load_case(case_path: Path) -> dict:
    with case_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HotPulse Agent on a local case.")
    parser.add_argument(
        "--case",
        help="Path to a case JSON file. Defaults to examples/cases/bridge_accident.json when no query is provided.",
    )
    parser.add_argument("--query", help="Ad-hoc event tracking question. Overrides --case when provided.")
    parser.add_argument("question", nargs="?", help="Ad-hoc event tracking question. Equivalent to --query.")
    parser.add_argument("--config", help="Optional path to a HotPulse JSON config file.")
    parser.add_argument(
        "--mode",
        default="offline",
        choices=["offline", "online"],
        help="Use local providers in offline mode, or keep configured providers in online mode. Defaults to offline.",
    )
    parser.add_argument("--search-provider", help="Override search provider, e.g. local, tavily, or serpapi.")
    parser.add_argument("--fetch-provider", help="Override fetch provider, e.g. local, firecrawl, or http.")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[2]
    query = args.query or args.question
    event_id = None
    if query:
        case = {"question": query}
    else:
        case_path = Path(args.case or "examples/cases/bridge_accident.json").expanduser()
        if not case_path.is_absolute():
            case_path = project_dir / case_path
        case = load_case(case_path)
        event_id = case.get("event_id")
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    config = load_config(base_dir=project_dir, config_path=config_path)
    if args.mode == "offline":
        config = override_config(config, search_provider="local", fetch_provider="local", policy_mode="rule")
    elif args.mode == "online":
        config = override_config(
            config,
            search_provider=args.search_provider or "tavily",
            fetch_provider=args.fetch_provider or "firecrawl",
        )
    elif args.search_provider or args.fetch_provider:
        config = override_config(config, search_provider=args.search_provider, fetch_provider=args.fetch_provider)
    print("== 配置 ==")
    print(f"检索 provider: {config.search.provider}")
    print(f"抓取 provider: {config.fetch.provider}")
    print(f"policy_mode: {config.policy.mode}")
    print(f"配置文件: {config_path if config_path else project_dir / 'hotpulse.config.json'}")
    orchestrator = build_orchestrator(project_dir, config=config)
    result = orchestrator.run(question=case["question"], event_id=event_id)

    print("== 计划 ==")
    print(result.plan.goal)
    for task in result.plan.sub_tasks:
        print(f"- {task.task_id}: {task.description} [{task.status}]")

    print("\n== 执行轨迹 ==")
    for trace in result.traces:
        print(
            f"步骤={trace.step_index} state={trace.state} tool={trace.tool_name} "
            f"原因={trace.reason} 观察={trace.observation}"
        )

    print("\n== 指标 ==")
    metrics = result.metrics
    print(f"证据数量: {metrics.get('evidence_count')}")
    print(f"来源数量: {metrics.get('source_diversity')}")
    print(f"覆盖度: {metrics.get('coverage')}")
    print(f"高可信证据数: {metrics.get('high_reliability_evidence')}")
    print(f"交叉验证证据数: {metrics.get('cross_verified_evidence')}")
    print(f"时间线事件数: {metrics.get('timeline_event_count')}")
    print(f"事件簇数: {metrics.get('event_cluster_count')}")
    print(f"计划置信度: {metrics.get('plan_confidence')}")
    print(f"记忆摘要: {metrics.get('memory_summary')}")
    print(f"检索词历史: {' -> '.join(metrics.get('query_history', []))}")
    print(f"路由决策来源: {', '.join(metrics.get('router_decision_sources', []))}")
    circuit_reason = metrics.get("policy_circuit_open_reason") or "无"
    print(f"策略熔断原因: {circuit_reason}")

    print("\n== 报告 ==")
    print(result.report)


if __name__ == "__main__":
    main()
