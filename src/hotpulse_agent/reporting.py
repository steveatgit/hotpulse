from __future__ import annotations

from .config import PolicyConfig
from .i18n import zh_source_type, zh_text
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.prompts import REPORT_SYSTEM_PROMPT, build_report_user_prompt


class RuleReportGenerator:
    def generate(self, question: str, memory: MemoryManager) -> str:
        confirmed = []
        for item in memory.evidence:
            if item.reliability >= 0.8:
                confirmed.append(
                    f"- [{item.evidence_id}] {zh_text(item.claim)}"
                    f"（{item.source}，{item.published_at}，可信度={item.reliability:.2f}）"
                )

        conflicts = memory.conflict_claims()
        if not conflicts:
            conflicts = ["当前证据中未发现明显的硬冲突。"]

        follow_ups = list(memory.unresolved_questions)
        if not follow_ups:
            follow_ups = [
                "继续关注官方机构是否发布最终调查更新。",
                "持续跟踪目击者表述与官方时间线是否完全收敛。",
            ]
        follow_ups = list(dict.fromkeys(follow_ups))

        summary = self._summary(memory)
        timeline_lines = self._timeline_lines(memory)
        cluster_lines = self._cluster_lines(memory)
        verification_lines = self._verification_lines(memory)
        source_lines = self._source_lines(memory)
        citation_lines = self._citation_lines(memory)
        snapshot_lines = self._snapshot_lines(memory)

        return "\n".join(
            [
                "# HotPulse 报告",
                "",
                "## 用户问题",
                question,
                "",
                "## 事件摘要",
                summary,
                "",
                "## 事件簇",
                *(cluster_lines or ["- 当前证据不足，尚未形成稳定事件簇。"]),
                "",
                "## 时间线",
                *(timeline_lines or ["- 当前证据不足，尚未形成稳定时间线。"]),
                "",
                "## 已确认事实",
                *(confirmed or ["- 当前已确认事实仍然有限。"]),
                "",
                "## 多源交叉验证",
                *(verification_lines or ["- 当前尚无足够多源证据完成交叉验证。"]),
                "",
                "## 冲突与不确定性",
                *[f"- {item}" for item in conflicts],
                "",
                "## 关键信源",
                *(source_lines or ["- 当前尚未收集到高可靠信源。"]),
                "",
                "## 增量快照",
                *(snapshot_lines or ["- 暂无增量快照。"]),
                "",
                "## 引用索引",
                *(citation_lines or ["- 暂无可追溯引用。"]),
                "",
                "## 后续建议",
                *[f"- {item}" for item in follow_ups],
            ]
        )

    def _summary(self, memory: MemoryManager) -> str:
        if not memory.evidence:
            return "当前证据不足，暂时无法生成可靠摘要。"
        best = sorted(memory.evidence, key=lambda item: (-item.reliability, item.published_at))[0]
        claim = zh_text(best.claim).rstrip("。.")
        return (
            f"当前证据显示，事件核心进展集中在：{claim}。"
            f"系统共收集到 {len(memory.evidence)} 条证据，覆盖 {memory.source_diversity()} 个来源，"
            f"形成 {len(memory.built_timeline)} 个时间线事件和 {len(memory.event_clusters)} 个事件簇。"
        )

    def _timeline_lines(self, memory: MemoryManager) -> list[str]:
        if memory.built_timeline:
            return [
                (
                    f"- {event.occurred_at} [{event.event_key}] {event.title}：{event.summary}"
                    f"（{event.verification_status}，证据={', '.join(event.evidence_ids)}）"
                )
                for event in memory.built_timeline
            ]

        fallback = []
        for item in sorted(memory.evidence, key=lambda e: e.published_at):
            fallback.append(f"- {item.published_at} [{item.evidence_id}] {item.source}：{zh_text(item.claim)}")
        return fallback

    def _cluster_lines(self, memory: MemoryManager) -> list[str]:
        return [
            (
                f"- [{cluster.cluster_id}] {cluster.label}：{cluster.summary}"
                f"（来源={len(cluster.sources)}，置信度={cluster.confidence:.2f}，更新时间={cluster.updated_at}）"
            )
            for cluster in memory.event_clusters
        ]

    def _verification_lines(self, memory: MemoryManager) -> list[str]:
        lines = []
        for event in memory.built_timeline:
            if event.verification_status in {"cross_verified", "primary_confirmed", "multi_source"}:
                lines.append(
                    f"- [{event.event_key}] {event.verification_status}："
                    f"{', '.join(event.sources)} 支撑 {', '.join(event.evidence_ids)}。"
                )
        return lines

    def _source_lines(self, memory: MemoryManager) -> list[str]:
        if memory.source_assessments:
            return [
                (
                    f"- {item.source}（{zh_source_type(item.source_type)}，"
                    f"可信度={item.reliability:.2f}，证据数={item.evidence_count}）："
                    f"{'；'.join(note.rstrip('。') for note in item.notes)}。"
                )
                for item in memory.source_assessments
            ]

        key_sources = []
        seen = set()
        for item in sorted(memory.evidence, key=lambda e: (-e.reliability, e.source)):
            if item.source not in seen:
                key_sources.append(f"- {item.source}（{zh_source_type(item.source_type)}，可信度={item.reliability:.2f}）")
                seen.add(item.source)
        return key_sources

    def _citation_lines(self, memory: MemoryManager) -> list[str]:
        lines = []
        for item in sorted(memory.evidence, key=lambda e: e.evidence_id):
            url = item.url or "local-corpus"
            text = zh_text(item.supporting_text).strip()
            if len(text) > 120:
                text = text[:117].rstrip() + "..."
            lines.append(f"- [{item.evidence_id}] {item.title}，{item.source}，{item.published_at}，{url}：{text}")
        return lines

    def _snapshot_lines(self, memory: MemoryManager) -> list[str]:
        snapshot = memory.incremental_snapshot
        if snapshot is None:
            return []
        return [
            f"- 最新发布时间：{snapshot.latest_published_at or '未知'}",
            f"- 当前证据/来源/时间线事件：{snapshot.evidence_count}/{snapshot.source_count}/{snapshot.timeline_event_count}",
            f"- 本轮新增证据：{', '.join(snapshot.new_evidence_ids) or '无'}",
        ]


class ReportGenerator:
    def __init__(self, policy: PolicyConfig | None = None) -> None:
        self.fallback = RuleReportGenerator()
        self.policy = policy
        self.component = "reporter"
        self.last_metadata: dict = {}
        self.client = LLMClient(policy) if policy and policy.mode_for(self.component) in {"llm", "hybrid"} else None

    def generate(self, question: str, memory: MemoryManager) -> str:
        if self.client is None:
            self.last_metadata = {"decision_source": "rule"}
            return self.fallback.generate(question, memory)
        try:
            user_prompt = build_report_user_prompt(question, memory)
            report = self.client.chat(REPORT_SYSTEM_PROMPT, user_prompt, component=self.component).strip()
            if not report:
                raise ValueError("Report generator returned empty text.")
            self.last_metadata = {"decision_source": "llm", "policy_call": self.client.last_metadata}
            return report
        except Exception as exc:
            self.last_metadata = {
                "decision_source": "rule-fallback",
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
                "policy_call": self.client.last_metadata if self.client else {},
            }
            return self.fallback.generate(question, memory)
