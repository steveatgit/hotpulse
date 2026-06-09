from __future__ import annotations

from dataclasses import dataclass, field

from .config import PolicyConfig
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.parser import parse_router_decision
from .policy.prompts import ROUTER_SYSTEM_PROMPT, build_router_user_prompt
from .policy.schemas import RouterDecision
from .schemas import AgentState, Plan


@dataclass
class ToolChoice:
    tool_name: str
    reason: str
    metadata: dict = field(default_factory=dict)


class RuleToolRouter:
    def choose(self, state: AgentState, plan: Plan, memory: MemoryManager) -> ToolChoice:
        if state in {AgentState.INIT, AgentState.PLANNING, AgentState.REPLANNING}:
            return ToolChoice("search_web", "深入分析前需要先获取候选文档。", {"decision_source": "rule"})

        if state == AgentState.SEARCHING:
            if not memory.candidate_docs:
                return ToolChoice("search_web", "尚未收集到候选文档，继续检索。", {"decision_source": "rule"})
            return ToolChoice("fetch_page", "已有检索结果，抓取优先级最高的未读来源。", {"decision_source": "rule"})

        if state == AgentState.READING:
            return ToolChoice("extract_evidence", "需要把已抓取页面内容规范化为证据。", {"decision_source": "rule"})

        if state == AgentState.EVIDENCE_EXTRACTING:
            if self._ready_for_timeline(memory) and not memory.built_timeline:
                return ToolChoice("build_timeline", "已有多源证据，可以先组织时间线。", {"decision_source": "rule"})
            if len(memory.evidence) >= 3 and memory.source_diversity() < 2 and self._has_unread_candidates(memory):
                return ToolChoice("search_web", "已有基础证据，但独立来源仍不足，继续补充第三方来源。", {"decision_source": "rule"})
            return ToolChoice("search_web", "构建时间线前还需要补充证据。", {"decision_source": "rule"})

        if state == AgentState.REFLECTING:
            if not self._ready_for_timeline(memory):
                return ToolChoice("search_web", "当前覆盖度还不够，需要继续检索。", {"decision_source": "rule"})
            if memory.source_diversity() < 3 and self._has_unread_candidates(memory):
                return ToolChoice("search_web", "还有未读独立来源，补充后再综合。", {"decision_source": "rule"})
            if not memory.built_timeline:
                return ToolChoice("build_timeline", "覆盖度已基本可用，整理事件时间线。", {"decision_source": "rule"})
            return ToolChoice("final_report", "证据和时间线已就绪，可以生成报告。", {"decision_source": "rule"})

        if state == AgentState.REPORTING:
            return ToolChoice("final_report", "报告生成条件已满足。", {"decision_source": "rule"})

        return ToolChoice("search_web", "默认回退到检索。", {"decision_source": "rule"})

    def _has_unread_candidates(self, memory: MemoryManager) -> bool:
        return any(doc.doc_id not in memory.fetched_docs for doc in memory.candidate_docs)

    def _ready_for_timeline(self, memory: MemoryManager) -> bool:
        return len(memory.evidence) >= 4 or (len(memory.evidence) >= 3 and memory.source_diversity() >= 2)


class HybridToolRouter:
    def __init__(self, policy: PolicyConfig, fallback: RuleToolRouter | None = None) -> None:
        self.policy = policy
        self.fallback = fallback or RuleToolRouter()
        self.component = "router"
        self.client = LLMClient(policy) if policy.mode_for(self.component) in {"llm", "hybrid"} else None

    def choose(self, state: AgentState, plan: Plan, memory: MemoryManager) -> ToolChoice:
        if self.policy.mode_for(self.component) == "rule":
            return self.fallback.choose(state, plan, memory)

        allowed_tools = self._allowed_tools(state, memory)
        try:
            llm_choice = self._llm_choose(state, plan, memory, allowed_tools)
            if llm_choice.selected_tool not in allowed_tools:
                raise ValueError(
                    f"Selected tool {llm_choice.selected_tool} violates state/policy constraints. allowed={allowed_tools}"
                )
            return ToolChoice(
                llm_choice.selected_tool,
                llm_choice.reason,
                {
                    "decision_source": "llm",
                    "router_confidence": llm_choice.confidence,
                    "allowed_tools": allowed_tools,
                    "policy_call": self.client.last_metadata if self.client else {},
                },
            )
        except Exception as exc:
            fallback_choice = self.fallback.choose(state, plan, memory)
            fallback_choice.metadata = {
                **fallback_choice.metadata,
                "decision_source": "rule-fallback",
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
                "allowed_tools": allowed_tools,
                "policy_call": self.client.last_metadata if self.client else {},
            }
            return fallback_choice

    def _llm_choose(
        self,
        state: AgentState,
        plan: Plan,
        memory: MemoryManager,
        allowed_tools: list[str],
    ) -> RouterDecision:
        if self.client is None:
            raise ValueError("LLM client is not initialized.")
        user_prompt = build_router_user_prompt(state, plan, memory, allowed_tools)
        raw_text = self.client.chat(ROUTER_SYSTEM_PROMPT, user_prompt, component=self.component)
        return parse_router_decision(raw_text)

    def _allowed_tools(self, state: AgentState, memory: MemoryManager) -> list[str]:
        if state in {AgentState.INIT, AgentState.PLANNING, AgentState.REPLANNING}:
            return ["search_web"]

        if state == AgentState.SEARCHING:
            return ["fetch_page"] if memory.candidate_docs else ["search_web"]

        if state == AgentState.READING:
            return ["extract_evidence"]

        if state == AgentState.EVIDENCE_EXTRACTING:
            if self.fallback._ready_for_timeline(memory) and not memory.built_timeline:
                return ["build_timeline", "search_web"]
            if len(memory.evidence) >= 3 and memory.source_diversity() < 2 and self.fallback._has_unread_candidates(memory):
                return ["search_web"]
            return ["search_web"]

        if state == AgentState.REFLECTING:
            if not self.fallback._ready_for_timeline(memory):
                return ["search_web"]
            if not memory.built_timeline:
                return ["build_timeline", "search_web"]
            return ["final_report", "search_web"]

        if state == AgentState.REPORTING:
            return ["final_report"]

        return ["search_web"]


ToolRouter = HybridToolRouter
