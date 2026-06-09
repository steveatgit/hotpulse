# HotPulse Agent

HotPulse 是一个用于热点事件演进查询的 Python agent MVP。当前版本以 LangGraph 为执行 harness，围绕“检索候选来源、抓取页面、抽取结构化证据、归并事件簇、构建时间线、多源交叉验证、生成带引用报告”这一条链路展开。

项目默认使用本地离线语料，方便稳定回放和评测；也可以切换到 Tavily/SerpApi 搜索与 Firecrawl/HTTP 抓取。

## 当前能力

- LangGraph 状态图执行：`initialize -> route_execute -> replan_reflect -> finalize`
- 在线 provider 诊断：`hotpulse-doctor` 检查搜索、抓取和 LLM policy 可用性
- 任务规划：识别范围、最新进展、冲突核验、时间线、报告生成
- 工具路由：按状态、证据覆盖度、来源多样性和预算选择下一步工具
- 结构化证据：每条证据带 `evidence_id`、来源、URL、可信度、claim 类型和支撑文本
- 事件簇归并：把相关证据合并为主题簇，例如伤亡、调查、谣言澄清
- 时间线抽取：生成 `TimelineEvent`，标注 `cross_verified`、`primary_confirmed` 等验证状态
- 来源评估：按来源类型、可靠度和证据数量评估关键信源
- 增量更新：按事件保存 `.runs/events/*.json` 档案，下一轮同一事件会标出新增证据和新增时间线事件
- 引用可追溯报告：报告结论引用 `evidence_id`，并附引用索引
- 离线回归评测：输出 evidence/source/timeline/cluster/trace 等过程指标

## 项目结构

```text
.
├── AGENTS.md                         # 仓库协作约定
├── README.md                         # 项目说明
├── hotpulse.config.json              # 本地默认配置，默认离线 provider
├── hotpulse.config.example.json      # 可复制的配置模板
├── pyproject.toml                    # 包配置与 console scripts
├── evals/
│   └── run_eval.py                   # eval CLI 入口包装
├── examples/
│   ├── cases/bridge_accident.json    # 默认离线 case
│   └── corpus/documents.json         # 默认离线语料
└── src/hotpulse_agent/
    ├── cli.py                        # hotpulse 命令入口
    ├── eval_cli.py                   # hotpulse-eval 命令入口
    ├── doctor.py                     # hotpulse-doctor provider 诊断入口
    ├── web.py                        # hotpulse-web 本地 HTTP UI
    ├── orchestrator.py               # LangGraph harness
    ├── planner.py                    # 任务规划与重规划
    ├── router.py                     # 工具路由
    ├── reflector.py                  # 反思与 query rewrite
    ├── memory.py                     # 运行态 memory 与证据存储
    ├── reporting.py                  # 报告生成
    ├── schemas.py                    # 核心 dataclass
    ├── config.py                     # JSON/env 配置加载
    ├── event_store.py                # 本地事件档案持久化
    ├── language.py                   # 语言与 query term helper
    ├── i18n.py                       # 离线 demo 中文展示映射
    ├── policy/                       # LLM policy prompt/client/parser
    └── tools/                        # search/fetch/extract/timeline tools
```

## 执行流程

```text
User Query
  -> Planner
  -> LangGraph route_execute loop
      -> search_web
      -> fetch_page
      -> extract_evidence
      -> build_timeline
  -> Reflector / Replanner
  -> Report Generator
  -> AgentResult(plan, traces, metrics, report)
```

LangGraph 节点定义在 `src/hotpulse_agent/orchestrator.py`：

```text
START
  -> initialize
  -> route_execute
  -> replan_reflect
      -> route_execute
      -> finalize
  -> END
```

## 安装

需要 Python 3.10+。

```bash
python3 -m pip install -e .
```

`pyproject.toml` 当前依赖：

- `langgraph>=1.0.0`

## 快速运行

运行默认离线 case：

```bash
hotpulse
```

等价于：

```bash
hotpulse --mode offline --case examples/cases/bridge_accident.json
```

运行临时问题：

```bash
hotpulse "请用中文追踪某个热点事件的最新进展、冲突说法和关键信源"
```

关闭本地事件档案：

```bash
hotpulse --no-event-store
```

指定事件档案目录：

```bash
hotpulse --event-store-dir /tmp/hotpulse-events
```

启动本地 Web UI：

```bash
hotpulse-web
```

默认地址是 `http://127.0.0.1:8765`。Web UI 使用同一套 planner/router/tools/reporter，并展示 plan、trace、metrics 和报告。

诊断在线 provider：

```bash
hotpulse-doctor
```

只检查配置不发起网络请求：

```bash
hotpulse-doctor --skip-network
```

指定配置文件：

```bash
hotpulse --config hotpulse.config.json
```

启动本地 Web 页面：

```bash
hotpulse-web
```

然后打开 `http://127.0.0.1:8765`。页面默认使用离线模式，提交后会展示配置摘要、计划、执行轨迹、指标和 Markdown 报告。也可以指定监听地址、端口和配置文件：

```bash
hotpulse-web --host 0.0.0.0 --port 8765 --config hotpulse.config.json
```

## 评测

只跑稳定离线规则策略：

```bash
hotpulse-eval --mode offline --policy-modes rule
```

比较多种 policy 模式：

```bash
hotpulse-eval --mode offline --policy-modes rule,hybrid,llm
```

Eval 输出字段包括：

- `state`
- `evidence_count`
- `source_diversity`
- `coverage`
- `citation_coverage`
- `cross_verified_evidence`
- `timeline_event_count`
- `event_cluster_count`
- `archive_loaded`
- `new_evidence_count`
- `new_timeline_event_count`
- `router_sources`
- `reflector_sources`
- fallback 标记

## 配置

默认配置文件是 `hotpulse.config.json`。如果要创建自己的配置，可以从示例复制：

```bash
cp hotpulse.config.example.json hotpulse.config.json
```

也可以使用环境变量覆盖关键字段：

```bash
export HOTPULSE_SEARCH_PROVIDER=tavily
export TAVILY_API_KEY=tvly-...
export HOTPULSE_FETCH_PROVIDER=firecrawl
export FIRECRAWL_API_KEY=fc-...
```

支持的 search provider：

- `local`
- `tavily`
- `serpapi`

支持的 fetch provider：

- `local`
- `firecrawl`
- `jina`
- `http`

支持的 policy mode：

- `rule`
- `hybrid`
- `llm`

`--mode offline` 会强制使用 `search=local`、`fetch=local`、`policy=rule`。`--mode online` 默认使用 `search=tavily`、`fetch=firecrawl`，policy 仍按配置文件或环境变量决定。在线检索会按配置的主 provider 优先执行，并在可用时用另一个搜索 provider 兜底：`tavily -> serpapi` 或 `serpapi -> tavily`。Firecrawl 抓取失败时会依次降级到 `jina`、`http`、搜索摘要原文。

Tavily 可以通过 domain policy 控制搜索源：

```json
{
  "search": {
    "tavily_include_domains": ["openai.com", "sec.gov", "reuters.com"],
    "tavily_exclude_domains": ["example.com"]
  }
}
```

LLM policy 使用 OpenAI-compatible chat completions。API key 优先级：

```text
HOTPULSE_LLM_API_KEY -> ZHIPU_API_KEY -> OPENAI_API_KEY
```

模型名优先级：

```text
HOTPULSE_LLM_MODEL -> ZHIPU_MODEL -> OPENAI_MODEL
```

`JINA_API_KEY` 用于 Jina Reader 授权；未配置时公共 Reader 端点仍可能可用，但稳定性和限额不应作为生产假设。

## 输出报告

CLI 会输出：

- 配置摘要
- 计划与子任务状态
- 执行轨迹
- 证据/来源/时间线/事件簇指标
- Markdown 报告

默认 CLI 会把事件档案写到 `.runs/events/`。该目录是运行产物，已被 `.gitignore` 忽略。`hotpulse-eval` 默认关闭 event store，避免回归结果受历史运行影响。

报告结构：

```text
# HotPulse 报告
## 用户问题
## 事件摘要
## 事件簇
## 时间线
## 已确认事实
## 多源交叉验证
## 冲突与不确定性
## 关键信源
## 增量快照
## 引用索引
## 后续建议
```

## 离线 Bridge Case

默认 case 是 `bridge_accident`，语料位于 `examples/corpus/documents.json`。它用于覆盖这些场景：

- 官方首次通报
- 医院补充伤者状态
- 媒体目击者说法
- 监管机构澄清死亡传言
- 调查仍在进行

当前离线规则策略通常会得到 7 条证据、3 个来源、5 个时间线事件和 5 个事件簇。首次 CLI 运行会把这些内容保存为事件档案；再次运行同一 `event_id` 时，增量快照会把已有证据与新增证据区分开。

## 扩展方向

优先级较高的后续工程：

- 增加更多离线 case，覆盖事件反转、官方更正、多源冲突、谣言扩散
- 加入更严格的 claim normalization 和 conflict detection
- 增加 snapshot replay eval，比较 trace 级回归
- 为 online 模式加入 freshness rerank 和 domain reliability policy
- 将事件簇升级为 temporal knowledge graph

## 安全

不要提交真实 API key、私有 endpoint 或线上运行记录。优先使用环境变量注入：

- `TAVILY_API_KEY`
- `SERPAPI_API_KEY`
- `FIRECRAWL_API_KEY`
- `JINA_API_KEY`
- `ZHIPU_API_KEY`
- `HOTPULSE_LLM_API_KEY`
