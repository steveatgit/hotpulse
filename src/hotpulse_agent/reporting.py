from __future__ import annotations

from collections import defaultdict

from .config import PolicyConfig
from .memory import MemoryManager
from .policy.client import LLMClient
from .policy.prompts import REPORT_SYSTEM_PROMPT, build_report_user_prompt


class RuleReportGenerator:
    def generate(self, question: str, memory: MemoryManager) -> str:
        timeline = defaultdict(list)
        for item in sorted(memory.evidence, key=lambda e: e.published_at):
            timeline[item.published_at].append(f"{item.source}: {item.claim}")

        confirmed = []
        for item in memory.evidence:
            if item.reliability >= 0.8:
                confirmed.append(f"- {item.claim}（{item.source}，{item.published_at}）")

        conflicts = memory.conflict_claims()
        if not conflicts:
            conflicts = ["当前证据中未发现明显的硬冲突。"]

        key_sources = []
        seen = set()
        for item in sorted(memory.evidence, key=lambda e: (-e.reliability, e.source)):
            if item.source not in seen:
                key_sources.append(f"- {item.source}（{item.source_type}，reliability={item.reliability:.2f}）")
                seen.add(item.source)

        follow_ups = list(memory.unresolved_questions)
        if not follow_ups:
            follow_ups = [
                "继续关注官方机构是否发布最终调查更新。",
                "持续跟踪目击者表述与官方时间线是否完全收敛。",
            ]
        follow_ups = list(dict.fromkeys(follow_ups))

        summary = self._summary(memory)
        timeline_lines = []
        for date_key, claims in timeline.items():
            timeline_lines.append(f"- {date_key}")
            for claim in claims[:3]:
                timeline_lines.append(f"  - {claim}")

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
                "## 时间线",
                *timeline_lines,
                "",
                "## 已确认事实",
                *(confirmed or ["- 当前已确认事实仍然有限。"]),
                "",
                "## 冲突与不确定性",
                *[f"- {item}" for item in conflicts],
                "",
                "## 关键信源",
                *(key_sources or ["- 当前尚未收集到高可靠信源。"]),
                "",
                "## 后续建议",
                *[f"- {item}" for item in follow_ups],
            ]
        )

    def _summary(self, memory: MemoryManager) -> str:
        if not memory.evidence:
            return "当前证据不足，暂时无法生成可靠摘要。"
        best = sorted(memory.evidence, key=lambda item: (-item.reliability, item.published_at))[0]
        claim = best.claim.rstrip(".")
        return (
            f"当前证据显示，事件核心进展集中在：{claim}。"
            f"系统共收集到 {len(memory.evidence)} 条证据，覆盖 {memory.source_diversity()} 个来源。"
        )


class ReportGenerator:
    def __init__(self, policy: PolicyConfig | None = None) -> None:
        self.fallback = RuleReportGenerator()
        self.policy = policy
        self.client = LLMClient(policy) if policy and policy.mode in {"llm", "hybrid"} else None

    def generate(self, question: str, memory: MemoryManager) -> str:
        if self.client is None:
            return self.fallback.generate(question, memory)
        try:
            user_prompt = build_report_user_prompt(question, memory)
            report = self.client.chat(REPORT_SYSTEM_PROMPT, user_prompt).strip()
            if not report:
                raise ValueError("Report generator returned empty text.")
            return report
        except Exception:
            return self.fallback.generate(question, memory)