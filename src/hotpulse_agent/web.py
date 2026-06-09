from __future__ import annotations

import argparse
from dataclasses import asdict
import errno
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .cli import build_orchestrator, load_case
from .config import load_config, override_config
from .schemas import AgentResult


DEFAULT_CASE = "examples/cases/bridge_accident.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the HotPulse local web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind. Defaults to 8765.")
    parser.add_argument("--config", help="Optional path to a HotPulse JSON config file.")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[2]
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    handler = _make_handler(project_dir=project_dir, config_path=config_path)
    server, bound_port = _bind_server(args.host, args.port, handler)
    print(f"HotPulse Web running at http://{args.host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHotPulse Web stopped.")
    finally:
        server.server_close()


def _bind_server(host: str, port: int, handler) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for candidate_port in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate_port), handler), candidate_port
        except OSError as exc:
            last_error = exc
            if exc.errno != errno.EADDRINUSE:
                raise
    raise OSError(f"No available port found in range {port}-{port + 19}") from last_error


def _make_handler(project_dir: Path, config_path: Path | None):
    class HotPulseWebHandler(BaseHTTPRequestHandler):
        server_version = "HotPulseWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_index_html())
                return
            if parsed.path == "/api/case":
                query = parse_qs(parsed.query)
                case_path = Path(query.get("path", [DEFAULT_CASE])[0]).expanduser()
                if not case_path.is_absolute():
                    case_path = project_dir / case_path
                try:
                    self._send_json(load_case(case_path))
                except Exception as exc:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/run":
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            try:
                request = self._read_json()
                response = _run_agent(project_dir=project_dir, config_path=config_path, request=request)
            except Exception as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(response)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length <= 0:
                return {}
            raw = self.rfile.read(content_length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            return payload

        def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

    return HotPulseWebHandler


def _run_agent(project_dir: Path, config_path: Path | None, request: dict[str, Any]) -> dict[str, Any]:
    mode = str(request.get("mode") or "offline").strip().lower()
    question = str(request.get("question") or "").strip()
    case_path_value = str(request.get("case_path") or DEFAULT_CASE).strip()
    event_id = request.get("event_id")

    if not question:
        case_path = Path(case_path_value).expanduser()
        if not case_path.is_absolute():
            case_path = project_dir / case_path
        case = load_case(case_path)
        question = str(case.get("question") or "").strip()
        event_id = case.get("event_id")
    if not question:
        raise ValueError("Question is required.")

    config = load_config(base_dir=project_dir, config_path=config_path)
    if mode == "offline":
        config = override_config(config, search_provider="local", fetch_provider="local", policy_mode="rule")
    elif mode == "online":
        config = override_config(
            config,
            search_provider=str(request.get("search_provider") or "tavily"),
            fetch_provider=str(request.get("fetch_provider") or "firecrawl"),
        )
    else:
        raise ValueError("Mode must be offline or online.")

    orchestrator = build_orchestrator(project_dir, config=config)
    result = orchestrator.run(question=question, event_id=str(event_id) if event_id else None)
    return {
        "question": question,
        "mode": mode,
        "config": {
            "search_provider": config.search.provider,
            "fetch_provider": config.fetch.provider,
            "policy_mode": config.policy.mode,
        },
        "result": _serialize_result(result),
    }


def _serialize_result(result: AgentResult) -> dict[str, Any]:
    return {
        "state": result.state.value,
        "plan": asdict(result.plan),
        "traces": [asdict(trace) for trace in result.traces],
        "metrics": result.metrics,
        "report": result.report,
    }


def _index_html() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HotPulse Web</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1d2430;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --warn: #b45309;
      --code: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .wrap {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
    }
    header .wrap {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 64px;
      gap: 16px;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }
    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 20px;
      padding: 20px 0 28px;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    aside {
      padding: 16px;
      align-self: start;
      position: sticky;
      top: 16px;
    }
    .content {
      display: grid;
      gap: 16px;
    }
    .block {
      padding: 16px;
    }
    label {
      display: block;
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }
    textarea, input, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      background: #fff;
      padding: 10px 11px;
      font: inherit;
    }
    textarea {
      min-height: 150px;
      resize: vertical;
    }
    .field { margin-bottom: 14px; }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    button {
      width: 100%;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 11px 14px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button:disabled {
      cursor: wait;
      opacity: .7;
    }
    .status {
      margin-top: 12px;
      min-height: 22px;
      color: var(--muted);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 78px;
      background: #fbfcfe;
    }
    .metric strong {
      display: block;
      font-size: 24px;
      line-height: 1.1;
      color: var(--accent-dark);
    }
    .metric span { color: var(--muted); font-size: 12px; }
    h2 {
      margin: 0 0 12px;
      font-size: 17px;
      letter-spacing: 0;
    }
    .task, .trace {
      border-top: 1px solid var(--line);
      padding: 10px 0;
    }
    .task:first-of-type, .trace:first-of-type { border-top: 0; }
    .pill {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      margin-left: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--code);
      font: 13px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .error { color: #b42318; }
    .muted { color: var(--muted); }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      aside { position: static; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>HotPulse Web</h1>
      <div class="muted" id="configText">本地热点事件演进追踪</div>
    </div>
  </header>
  <div class="wrap">
    <main>
      <aside>
        <form id="runForm">
          <div class="field">
            <label for="question">问题</label>
            <textarea id="question" name="question">请用中文追踪桥梁事故的最新进展、冲突说法和关键信源</textarea>
          </div>
          <div class="row">
            <div class="field">
              <label for="mode">模式</label>
              <select id="mode" name="mode">
                <option value="offline" selected>offline</option>
                <option value="online">online</option>
              </select>
            </div>
            <div class="field">
              <label for="casePath">Case</label>
              <input id="casePath" name="casePath" value="examples/cases/bridge_accident.json">
            </div>
          </div>
          <button id="runButton" type="submit">运行分析</button>
          <div class="status" id="status">准备就绪</div>
        </form>
      </aside>
      <div class="content">
        <section class="block">
          <h2>指标</h2>
          <div class="metrics" id="metrics"></div>
        </section>
        <section class="block">
          <h2>计划</h2>
          <div id="plan" class="muted">尚未运行</div>
        </section>
        <section class="block">
          <h2>执行轨迹</h2>
          <div id="traces" class="muted">尚未运行</div>
        </section>
        <section class="block">
          <h2>报告</h2>
          <pre id="report">尚未运行</pre>
        </section>
      </div>
    </main>
  </div>
  <script>
    const form = document.querySelector("#runForm");
    const statusEl = document.querySelector("#status");
    const runButton = document.querySelector("#runButton");
    const metricsEl = document.querySelector("#metrics");
    const planEl = document.querySelector("#plan");
    const tracesEl = document.querySelector("#traces");
    const reportEl = document.querySelector("#report");
    const configText = document.querySelector("#configText");

    function setStatus(text, isError = false) {
      statusEl.textContent = text;
      statusEl.className = isError ? "status error" : "status";
    }

    function renderMetrics(metrics) {
      const items = [
        ["证据", metrics.evidence_count],
        ["来源", metrics.source_diversity],
        ["时间线", metrics.timeline_event_count],
        ["事件簇", metrics.event_cluster_count],
        ["引用覆盖", metrics.citation_coverage],
        ["高可信", metrics.high_reliability_evidence],
        ["交叉验证", metrics.cross_verified_evidence],
        ["历史档案", metrics.archive_loaded ? "是" : "否"],
        ["新增证据", metrics.new_evidence_count],
        ["新增时间线", metrics.new_timeline_event_count],
        ["覆盖度", metrics.coverage],
      ];
      metricsEl.innerHTML = items.map(([label, value]) => `
        <div class="metric"><strong>${value ?? "-"}</strong><span>${label}</span></div>
      `).join("");
    }

    function renderPlan(plan) {
      planEl.className = "";
      planEl.innerHTML = `
        <div><strong>${escapeHtml(plan.goal)}</strong><span class="pill">confidence ${plan.confidence}</span></div>
        ${(plan.sub_tasks || []).map(task => `
          <div class="task">
            <strong>${escapeHtml(task.task_id)}</strong>
            <span class="pill">${escapeHtml(task.status)}</span>
            <div>${escapeHtml(task.description)}</div>
          </div>
        `).join("")}
      `;
    }

    function renderTraces(traces) {
      tracesEl.className = "";
      tracesEl.innerHTML = (traces || []).map(trace => `
        <div class="trace">
          <strong>#${trace.step_index} ${escapeHtml(trace.tool_name)}</strong>
          <span class="pill">${escapeHtml(trace.state)}</span>
          <div>${escapeHtml(trace.reason)}</div>
          <div class="muted">${escapeHtml(trace.observation)}</div>
        </div>
      `).join("");
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      runButton.disabled = true;
      setStatus("运行中...");
      try {
        const payload = {
          question: document.querySelector("#question").value,
          mode: document.querySelector("#mode").value,
          case_path: document.querySelector("#casePath").value,
        };
        const response = await fetch("/api/run", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "运行失败");
        const result = data.result;
        configText.textContent = `${data.config.search_provider} / ${data.config.fetch_provider} / ${data.config.policy_mode}`;
        renderMetrics(result.metrics || {});
        renderPlan(result.plan || {});
        renderTraces(result.traces || []);
        reportEl.textContent = result.report || "";
        setStatus(`完成：${result.state}`);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        runButton.disabled = false;
      }
    });
  </script>
</body>
</html>"""


if __name__ == "__main__":
    main()
