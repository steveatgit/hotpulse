# Repository Guidelines

## Project Structure & Module Organization

HotPulse is a Python agentic search MVP using a `src` layout. Core code lives in `src/hotpulse_agent/`: orchestration in `orchestrator.py`, state and dataclasses in `schemas.py`, CLI wiring in `cli.py`, planning/reflection/routing in their named modules, and tool implementations under `tools/`. LLM policy helpers live in `policy/`. Offline demo data is in `examples/corpus/`, runnable cases are in `examples/cases/`, and the lightweight evaluation harness is `evals/run_eval.py`.

## Build, Test, and Development Commands

Run commands from the repository root.

```bash
python3 -m pip install -e .
```

Installs the package in editable mode so local commands can run without `PYTHONPATH=src`.

```bash
hotpulse
```

Runs the default local case and prints config, plan, trace, metrics, and final report.

```bash
hotpulse \
  --config hotpulse.config.json
```

Runs with an explicit config file.

```bash
hotpulse-eval --mode offline
```

Runs offline policy comparisons across `examples/cases/`.

## Coding Style & Naming Conventions

Use Python 3.10+ with type hints and `from __future__ import annotations`, matching existing modules. Prefer dataclasses for structured runtime objects. Keep functions small and explicit: planners, routers, reflectors, tools, and reporters should remain separately testable. Use 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and descriptive tool names such as `BuildTimelineTool`.

## Testing Guidelines

There is no dedicated unit-test suite yet. Treat `evals/run_eval.py --mode offline` as the required regression check before changes that affect planning, routing, memory, extraction, or reporting. Add new case JSON files under `examples/cases/` and matching documents in `examples/corpus/documents.json` when covering new event patterns. Keep eval output deterministic in offline mode.

## Commit & Pull Request Guidelines

The current history uses short imperative messages, for example `init` and `run success`. Continue with concise, action-oriented commit subjects such as `add offline eval case` or `fix timeline ordering`. Pull requests should include a short behavior summary, commands run, affected cases or providers, and screenshots only when UI or report formatting changes.

## Security & Configuration Tips

Do not commit real API keys or private endpoints. Prefer environment variables for `TAVILY_API_KEY`, `SERPAPI_API_KEY`, `FIRECRAWL_API_KEY`, and policy provider credentials. When sharing configs, use placeholders and keep online-provider changes separate from offline regression work.
