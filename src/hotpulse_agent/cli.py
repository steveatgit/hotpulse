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
        default="examples/cases/bridge_accident.json",
        help="Path to a case JSON file. Defaults to examples/cases/bridge_accident.json.",
    )
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
    case_path = Path(args.case).expanduser()
    if not case_path.is_absolute():
        case_path = project_dir / case_path
    case = load_case(case_path)
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    config = load_config(base_dir=project_dir, config_path=config_path)
    if args.mode == "offline":
        config = override_config(config, search_provider="local", fetch_provider="local", policy_mode="rule")
    elif args.search_provider or args.fetch_provider:
        config = override_config(config, search_provider=args.search_provider, fetch_provider=args.fetch_provider)
    print("== CONFIG ==")
    print(f"search_provider: {config.search.provider}")
    print(f"fetch_provider: {config.fetch.provider}")
    print(f"policy_mode: {config.policy.mode}")
    print(f"config_path: {config_path if config_path else project_dir / 'hotpulse.config.json'}")
    orchestrator = build_orchestrator(project_dir, config=config)
    result = orchestrator.run(question=case["question"], event_id=case.get("event_id"))

    print("== PLAN ==")
    print(result.plan.goal)
    for task in result.plan.sub_tasks:
        print(f"- {task.task_id}: {task.description} [{task.status}]")

    print("\n== TRACE ==")
    for trace in result.traces:
        print(
            f"step={trace.step_index} state={trace.state} tool={trace.tool_name} "
            f"reason={trace.reason} obs={trace.observation}"
        )

    print("\n== METRICS ==")
    for key, value in result.metrics.items():
        print(f"{key}: {value}")

    print("\n== REPORT ==")
    print(result.report)


if __name__ == "__main__":
    main()
