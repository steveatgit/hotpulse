from __future__ import annotations

import json

from ..language import preferred_language
from ..memory import MemoryManager
from ..schemas import AgentState, Plan


ROUTER_SYSTEM_PROMPT = """你是 HotPulse Agent 的工具选择模块。
你必须从给定候选工具中选择下一步最合适的工具，并只输出一个 JSON 对象。

输出字段必须包含：
- selected_tool: 字符串，必须是候选工具之一
- reason: 字符串，简洁说明选择原因
- confidence: 0 到 1 之间的小数

禁止输出 markdown、代码块或额外解释。"""


PLANNER_SYSTEM_PROMPT = """你是 HotPulse Agent 的任务规划模块。
你要把热点事件追踪问题转换成结构化计划，或者根据当前证据状态更新计划。
你只能输出一个 JSON 对象。

输出字段必须包含：
- scope: 字符串
- sub_tasks: 数组，每项包含 task_id、description、target、status
- stop_conditions: 字符串数组
- open_questions: 字符串数组
- confidence: 0 到 1 之间的小数

task_id 只能使用：
- scope
- updates
- conflicts
- timeline
- report

禁止输出 markdown、代码块或额外解释。"""


REFLECTOR_SYSTEM_PROMPT = """你是 HotPulse Agent 的反思与纠偏模块。
你要根据当前计划、memory 状态、最近失败信号和预算，判断是否需要补搜、改写 query 或重规划。
你只能输出一个 JSON 对象。

输出字段必须包含：
- should_replan: 布尔值
- should_rewrite_query: 布尔值
- should_continue: 布尔值
- next_query: 字符串，没有改写时返回当前 query
- notes: 字符串数组，说明判断依据
- confidence: 0 到 1 之间的小数

如果用户问题和当前 query 以中文为主，那么 next_query 也必须使用中文，不要改写成英文布尔检索式。

禁止输出 markdown、代码块或额外解释。"""


REPORT_SYSTEM_PROMPT = """你是 HotPulse Agent 的中文报告生成模块。
你要基于给定的 evidence、timeline 和 unresolved questions，输出一份简洁、可信、结构化的中文报告。
要求：
- 全文使用中文
- 可以保留必要的英文专有名词、机构名和网页标题
- 不要编造事实，不要补充 evidence 中不存在的信息
- 保持以下 Markdown 结构：
  # HotPulse 报告
  ## 用户问题
  ## 事件摘要
  ## 时间线
  ## 已确认事实
  ## 冲突与不确定性
  ## 关键信源
  ## 后续建议
"""


def build_router_user_prompt(state: AgentState, plan: Plan, memory: MemoryManager, allowed_tools: list[str]) -> str:
    summary = {
        "state": state.value,
        "goal": plan.goal,
        "scope": plan.scope,
        "open_questions": plan.open_questions,
        "sub_tasks": [
            {"task_id": task.task_id, "status": task.status, "target": task.target}
            for task in plan.sub_tasks
        ],
        "memory": {
            "candidate_docs": len(memory.candidate_docs),
            "fetched_docs": len(memory.fetched_docs),
            "evidence_count": len(memory.evidence),
            "source_diversity": memory.source_diversity(),
            "built_timeline": bool(memory.built_timeline),
            "last_tool": memory.working.last_tool,
            "remaining_steps": memory.working.remaining_steps,
            "query_history": memory.working.query_history[-3:],
            "top_entities": memory.top_entities(limit=4),
            "recent_reflections": memory.recent_reflection_messages(limit=3),
        },
        "allowed_tools": allowed_tools,
        "tool_rules": {
            "search_web": "当证据不足、来源不足或需要扩搜时使用",
            "fetch_page": "当已有候选文档并需要读取具体页面时使用",
            "extract_evidence": "当已抓取页面但还未抽取结构化证据时使用",
            "build_timeline": "当证据较充分且需要组织时间线时使用",
            "final_report": "当证据和时间线都足够时使用",
        },
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def build_reflector_user_prompt(plan: Plan, memory: MemoryManager, current_query: str) -> str:
    summary = {
        "goal": plan.goal,
        "scope": plan.scope,
        "open_questions": plan.open_questions,
        "current_query": current_query,
        "preferred_query_language": preferred_language(plan.goal),
        "memory": {
            "candidate_docs": len(memory.candidate_docs),
            "fetched_docs": len(memory.fetched_docs),
            "evidence_count": len(memory.evidence),
            "source_diversity": memory.source_diversity(),
            "built_timeline": bool(memory.built_timeline),
            "remaining_steps": memory.working.remaining_steps,
            "query_history": memory.working.query_history[-3:],
            "top_entities": memory.top_entities(limit=4),
            "recent_reflections": memory.recent_reflection_messages(limit=3),
            "conflict_claims": memory.conflict_claims(),
        },
        "policy_hints": {
            "rewrite_query_when": [
                "来源过于单一",
                "存在明显冲突，需要验证",
                "检索路径已经饱和但证据不足",
            ],
            "continue_when": [
                "证据数量仍不足",
                "高可靠来源不足",
                "关键冲突尚未验证",
            ],
            "be_conservative_when": [
                "仍有未抽取的已抓取文档",
                "预算接近耗尽",
            ],
        },
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def build_report_user_prompt(question: str, memory: MemoryManager) -> str:
    timeline = {}
    for item in sorted(memory.evidence, key=lambda evidence: evidence.published_at):
        timeline.setdefault(item.published_at, []).append(
            {
                "source": item.source,
                "claim": item.claim,
                "title": item.title,
                "reliability": item.reliability,
            }
        )

    payload = {
        "question": question,
        "preferred_answer_language": "zh",
        "memory_summary": memory.summary(),
        "evidence_count": len(memory.evidence),
        "source_diversity": memory.source_diversity(),
        "conflict_claims": memory.conflict_claims(),
        "unresolved_questions": memory.unresolved_questions,
        "timeline": timeline,
        "evidence": [
            {
                "claim": item.claim,
                "source": item.source,
                "source_type": item.source_type,
                "published_at": item.published_at,
                "reliability": item.reliability,
                "title": item.title,
            }
            for item in memory.evidence[:12]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

def build_planner_create_prompt(question: str, event_id: str | None) -> str:
    payload = {
        "question": question,
        "event_id": event_id,
        "required_task_ids": ["scope", "updates", "conflicts", "timeline", "report"],
        "task_requirements": {
            "scope": "识别事件范围、时间窗口和可能别名",
            "updates": "搜集最新关键进展",
            "conflicts": "识别并验证冲突说法",
            "timeline": "构建事件时间线",
            "report": "生成 grounded 报告",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_planner_replan_prompt(plan: Plan, memory: MemoryManager) -> str:
    payload = {
        "current_plan": {
            "goal": plan.goal,
            "scope": plan.scope,
            "stop_conditions": plan.stop_conditions,
            "open_questions": plan.open_questions,
            "sub_tasks": [
                {
                    "task_id": task.task_id,
                    "description": task.description,
                    "target": task.target,
                    "status": task.status,
                }
                for task in plan.sub_tasks
            ],
        },
        "memory": {
            "candidate_docs": len(memory.candidate_docs),
            "fetched_docs": len(memory.fetched_docs),
            "evidence_count": len(memory.evidence),
            "source_diversity": memory.source_diversity(),
            "built_timeline": bool(memory.built_timeline),
            "top_entities": memory.top_entities(limit=4),
            "recent_reflections": memory.recent_reflection_messages(limit=4),
            "conflict_claims": memory.conflict_claims(),
            "remaining_steps": memory.working.remaining_steps,
        },
        "required_task_ids": ["scope", "updates", "conflicts", "timeline", "report"],
        "allowed_status": ["pending", "completed", "ready"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)