from __future__ import annotations

from dataclasses import asdict
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .memory import MemoryManager
from .planner import Planner
from .reflector import Reflector
from .reporting import ReportGenerator
from .router import ToolRouter
from .schemas import AgentResult, AgentState, Observation, Plan, StepTrace
from .tools.registry import ToolRegistry


class OrchestratorGraphState(TypedDict, total=False):
    question: str
    event_id: str | None
    memory: MemoryManager
    agent_state: AgentState
    plan: Plan
    traces: list[StepTrace]
    reflection_decisions: list[dict]
    planning_decisions: list[dict]
    step_index: int
    last_tool_name: str
    should_finish: bool
    report: str
    final_state: AgentState
    metrics: dict


class HotPulseOrchestrator:
    def __init__(
        self,
        planner: Planner,
        reflector: Reflector,
        router: ToolRouter,
        registry: ToolRegistry,
        reporter: ReportGenerator,
        max_steps: int = 12,
    ) -> None:
        self.planner = planner
        self.reflector = reflector
        self.router = router
        self.registry = registry
        self.reporter = reporter
        self.max_steps = max_steps
        self.graph = self._build_graph()

    def run(self, question: str, event_id: str | None = None) -> AgentResult:
        graph_state = self.graph.invoke({"question": question, "event_id": event_id})
        return AgentResult(
            plan=graph_state["plan"],
            report=graph_state["report"],
            state=graph_state["final_state"],
            traces=graph_state["traces"],
            metrics=graph_state["metrics"],
        )

    def _build_graph(self):
        builder = StateGraph(OrchestratorGraphState)
        builder.add_node("initialize", self._initialize_node)
        builder.add_node("route_execute", self._route_execute_node)
        builder.add_node("replan_reflect", self._replan_reflect_node)
        builder.add_node("finalize", self._finalize_node)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "route_execute")
        builder.add_edge("route_execute", "replan_reflect")
        builder.add_conditional_edges(
            "replan_reflect",
            self._next_after_reflection,
            {
                "continue": "route_execute",
                "finalize": "finalize",
            },
        )
        builder.add_edge("finalize", END)
        return builder.compile()

    def _initialize_node(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        question = graph_state["question"]
        event_id = graph_state.get("event_id")
        memory = MemoryManager()
        memory.working.current_objective = question
        memory.working.current_query = question
        memory.record_query(question)
        memory.working.remaining_steps = self.max_steps

        plan = self.planner.create_plan(question, event_id)
        return {
            "question": question,
            "event_id": event_id,
            "memory": memory,
            "agent_state": AgentState.INIT,
            "plan": plan,
            "traces": [],
            "reflection_decisions": [],
            "planning_decisions": [dict(plan.metadata)],
            "step_index": 0,
            "last_tool_name": "",
            "should_finish": False,
        }

    def _route_execute_node(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        question = graph_state["question"]
        event_id = graph_state.get("event_id")
        memory = graph_state["memory"]
        plan = graph_state["plan"]
        agent_state = graph_state["agent_state"]
        step_index = graph_state["step_index"] + 1
        memory.working.remaining_steps = self.max_steps - step_index

        if agent_state in {AgentState.INIT, AgentState.REPLANNING}:
            agent_state = AgentState.PLANNING

        choice = self.router.choose(agent_state, plan, memory)
        tool_name, reason = choice.tool_name, choice.reason
        observation = self._execute_tool(tool_name, question, event_id, plan, memory)
        traces = list(graph_state["traces"])
        traces.append(
            StepTrace(
                step_index=step_index,
                state=agent_state.value,
                tool_name=tool_name,
                reason=reason,
                observation=observation.summary,
                metadata={**choice.metadata, **observation.payload.get("trace_metadata", {})},
            )
        )

        self._apply_observation(observation, memory)
        agent_state = self._state_after_tool(tool_name)
        return {
            "memory": memory,
            "agent_state": agent_state,
            "traces": traces,
            "step_index": step_index,
            "last_tool_name": tool_name,
        }

    def _replan_reflect_node(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        question = graph_state["question"]
        memory = graph_state["memory"]
        plan = self.planner.replan(graph_state["plan"], memory)
        planning_decisions = list(graph_state["planning_decisions"])
        planning_decisions.append(dict(plan.metadata))
        memory.unresolved_questions = list(plan.open_questions)

        reflection = self.reflector.assess(plan, memory)
        reflection_decisions = list(graph_state["reflection_decisions"])
        reflection_decisions.append(
            {
                "decision_source": reflection.metadata.get("decision_source", "unknown"),
                "should_replan": reflection.should_replan,
                "should_rewrite_query": reflection.should_rewrite_query,
                "should_continue": reflection.should_continue,
                "next_query": reflection.next_query,
                "notes": reflection.notes,
                "metadata": reflection.metadata,
            }
        )
        for note in reflection.notes:
            memory.add_reflection("reflection", note)

        agent_state = graph_state["agent_state"]
        if reflection.should_rewrite_query and reflection.next_query != memory.working.current_query:
            memory.working.current_query = reflection.next_query
            memory.record_query(reflection.next_query)
            agent_state = AgentState.REPLANNING

        should_finish = False
        if self.planner.should_stop(memory):
            agent_state = AgentState.REPORTING
            should_finish = True
        elif graph_state["step_index"] >= self.max_steps - 1:
            memory.add_reflection("budget", "由于步骤预算即将耗尽，转入报告生成。")
            agent_state = AgentState.REPORTING
            should_finish = True
        elif graph_state.get("last_tool_name") == "build_timeline" and len(memory.evidence) < 4:
            agent_state = AgentState.REPLANNING

        if not reflection.should_continue:
            agent_state = AgentState.REPORTING
            should_finish = True

        return {
            "memory": memory,
            "plan": plan,
            "agent_state": agent_state,
            "planning_decisions": planning_decisions,
            "reflection_decisions": reflection_decisions,
            "should_finish": should_finish,
        }

    def _next_after_reflection(self, graph_state: OrchestratorGraphState) -> Literal["continue", "finalize"]:
        return "finalize" if graph_state.get("should_finish") else "continue"

    def _finalize_node(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        question = graph_state["question"]
        memory = graph_state["memory"]
        plan = graph_state["plan"]
        traces = graph_state["traces"]
        planning_decisions = graph_state["planning_decisions"]
        reflection_decisions = graph_state["reflection_decisions"]
        report = self.reporter.generate(question, memory)
        final_state = AgentState.DONE if memory.evidence else AgentState.FAILED
        metrics = {
            "evidence_count": len(memory.evidence),
            "source_diversity": memory.source_diversity(),
            "coverage": memory.evidence_coverage(),
            "high_reliability_evidence": len(memory.high_reliability_evidence()),
            "cross_verified_evidence": len(memory.cross_verified_evidence()),
            "timeline_event_count": len(memory.built_timeline),
            "event_cluster_count": len(memory.event_clusters),
            "source_assessment_count": len(memory.source_assessments),
            "incremental_snapshot": asdict(memory.incremental_snapshot) if memory.incremental_snapshot else {},
            "memory_summary": memory.summary(),
            "plan_confidence": plan.confidence,
            "plan_metadata": plan.metadata,
            "planning_decisions": planning_decisions,
            "query_history": memory.working.query_history,
            "router_decision_sources": [trace.metadata.get("decision_source", "unknown") for trace in traces],
            "reflection_decisions": reflection_decisions,
            "report_decision": getattr(self.reporter, "last_metadata", {}),
            "policy_call_history": getattr(getattr(self.reporter, "policy", None), "call_history", []),
            "policy_circuit_open_reason": getattr(getattr(self.reporter, "policy", None), "circuit_open_reason", ""),
            "reflections": [asdict(note) for note in memory.reflections],
            "sub_tasks": [asdict(task) for task in plan.sub_tasks],
        }
        return {
            "report": report,
            "final_state": final_state,
            "metrics": metrics,
        }

    def _state_after_tool(self, tool_name: str) -> AgentState:
        if tool_name == "search_web":
            return AgentState.SEARCHING
        if tool_name == "fetch_page":
            return AgentState.READING
        if tool_name == "extract_evidence":
            return AgentState.EVIDENCE_EXTRACTING
        if tool_name == "build_timeline":
            return AgentState.REFLECTING
        if tool_name == "final_report":
            return AgentState.REPORTING
        return AgentState.SEARCHING

    def _execute_tool(
        self,
        tool_name: str,
        question: str,
        event_id: str | None,
        plan: Plan,
        memory: MemoryManager,
    ) -> Observation:
        if tool_name == "search_web":
            tool = self.registry.get(tool_name)
            docs = tool.run(query=memory.working.current_query, event_id=event_id)
            summary = f"检索到 {len(docs)} 篇候选文档。"
            return Observation(tool_name=tool_name, summary=summary, payload={"docs": docs})

        if tool_name == "fetch_page":
            tool = self.registry.get(tool_name)
            fetch_result = tool.run(docs=memory.next_fetch_candidates(limit=3), fetched_doc_ids=set(memory.fetched_docs))
            if fetch_result is None:
                return Observation(tool_name=tool_name, summary="没有剩余未读文档。", payload={})
            doc = fetch_result["doc"]
            fetch_mode = fetch_result.get("fetch_mode", "unknown")
            summary = f"通过 {fetch_mode} 从 {doc.source} 抓取 {doc.doc_id}。"
            return Observation(
                tool_name=tool_name,
                summary=summary,
                payload={
                    "doc": doc,
                    "trace_metadata": {"fetch_mode": fetch_mode},
                },
            )

        if tool_name == "extract_evidence":
            tool = self.registry.get(tool_name)
            unread_docs = [doc for doc_id, doc in memory.fetched_docs.items() if doc_id not in {e.doc_id for e in memory.evidence}]
            if not unread_docs:
                return Observation(tool_name=tool_name, summary="没有需要抽取的已抓取文档。", payload={})
            doc = unread_docs[0]
            evidence = tool.run(doc=doc)
            summary = f"从 {doc.doc_id} 抽取到 {len(evidence)} 条证据。"
            return Observation(tool_name=tool_name, summary=summary, payload={"evidence": evidence})

        if tool_name == "build_timeline":
            tool = self.registry.get(tool_name)
            timeline = tool.run(evidence=memory.evidence, open_questions=plan.open_questions)
            summary = (
                f"构建了 {len(timeline.events)} 个时间线事件、"
                f"{len(timeline.clusters)} 个事件簇，并评估 {len(timeline.source_assessments)} 个信源。"
            )
            return Observation(tool_name=tool_name, summary=summary, payload={"timeline": timeline})

        if tool_name == "final_report":
            relevant = memory.retrieve_relevant_evidence(query=question, limit=3)
            summary = f"基于 {len(relevant)} 条高相关证据准备最终综合。"
            return Observation(tool_name=tool_name, summary=summary, payload={"relevant_evidence": relevant})

        return Observation(
            tool_name=tool_name,
            summary=f"选择了不支持的工具：{tool_name}。",
            payload={"question": question, "plan_goal": plan.goal},
        )

    def _apply_observation(self, observation: Observation, memory: MemoryManager) -> None:
        memory.working.last_tool = observation.tool_name
        if observation.tool_name == "search_web":
            docs = observation.payload.get("docs", [])
            memory.add_candidates(docs)
            if not docs:
                memory.add_reflection("search_failure", "检索没有返回候选文档。")
        elif observation.tool_name == "fetch_page":
            doc = observation.payload.get("doc")
            if doc is not None:
                memory.add_fetched(doc)
            else:
                memory.add_reflection("fetch_failure", "没有更多文档可抓取。")
        elif observation.tool_name == "extract_evidence":
            evidence = observation.payload.get("evidence", [])
            memory.add_evidence(evidence)
            if not evidence:
                memory.add_reflection("extraction_failure", "已抓取页面中没有抽取到证据。")
        elif observation.tool_name == "build_timeline":
            timeline = observation.payload.get("timeline", {})
            if not timeline:
                memory.add_reflection("timeline_failure", "时间线构建器没有返回结果。")
            else:
                memory.apply_timeline_result(
                    timeline=timeline.events,
                    clusters=timeline.clusters,
                    source_assessments=timeline.source_assessments,
                    snapshot=timeline.snapshot,
                )
