from __future__ import annotations

from collections import Counter, defaultdict
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..i18n import zh_text
from ..schemas import (
    EventCluster,
    Evidence,
    IncrementalSnapshot,
    SourceAssessment,
    TimelineBuildResult,
    TimelineEvent,
)
from .base import BaseTool


class BuildTimelineTool(BaseTool):
    name = "build_timeline"

    def run(self, evidence: list[Evidence], open_questions: list[str] | None = None) -> TimelineBuildResult:
        grouped = self._group_evidence(evidence)
        events = self._build_events(grouped)
        clusters = self._build_clusters(events, evidence)
        source_assessments = self._assess_sources(evidence)
        snapshot = IncrementalSnapshot(
            latest_published_at=max((self._evidence_date_key(item) for item in evidence), default=""),
            evidence_count=len(evidence),
            source_count=len({item.source for item in evidence}),
            timeline_event_count=len(events),
            new_evidence_ids=[item.evidence_id for item in evidence if item.evidence_id],
            open_questions=list(open_questions or []),
        )
        return TimelineBuildResult(
            events=events,
            clusters=clusters,
            source_assessments=source_assessments,
            snapshot=snapshot,
        )

    def _group_evidence(self, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
        grouped: dict[str, list[Evidence]] = defaultdict(list)
        for item in sorted(evidence, key=lambda entry: (self._evidence_date_key(entry), entry.source)):
            key = "|".join([self._evidence_date_key(item), self._topic_key(item)])
            grouped[key].append(item)
        return dict(grouped)

    def _build_events(self, grouped: dict[str, list[Evidence]]) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        for index, (group_key, items) in enumerate(grouped.items(), start=1):
            date_key, topic = group_key.split("|", 1)
            sources = sorted({item.source for item in items})
            claim_types = [topic]
            claim_types.extend(
                item
                for item in sorted({item.claim_type for item in items if item.claim_type})
                if item != topic
            )
            confidence = self._confidence(items)
            status = self._verification_status(items)
            best = sorted(items, key=lambda item: (-item.reliability, item.published_at))[0]
            events.append(
                TimelineEvent(
                    event_key=f"T{index:03d}",
                    occurred_at=date_key,
                    title=self._event_title(topic, best),
                    summary=zh_text(best.claim),
                    evidence_ids=[item.evidence_id for item in items if item.evidence_id],
                    sources=sources,
                    confidence=confidence,
                    verification_status=status,
                    claim_types=claim_types or ["update"],
                )
            )
        events.sort(key=lambda item: (item.occurred_at, item.event_key))
        return events

    def _build_clusters(self, events: list[TimelineEvent], evidence: list[Evidence]) -> list[EventCluster]:
        evidence_by_id = {item.evidence_id: item for item in evidence if item.evidence_id}
        grouped: dict[str, list[TimelineEvent]] = defaultdict(list)
        for event in events:
            topic = event.claim_types[0] if event.claim_types else "update"
            grouped[topic].append(event)

        clusters: list[EventCluster] = []
        for index, (topic, topic_events) in enumerate(sorted(grouped.items()), start=1):
            evidence_ids = []
            sources: set[str] = set()
            entities: Counter = Counter()
            for event in topic_events:
                evidence_ids.extend(event.evidence_ids)
                sources.update(event.sources)
                for evidence_id in event.evidence_ids:
                    item = evidence_by_id.get(evidence_id)
                    if item:
                        entities.update(item.entities)
            confidence = round(
                sum(event.confidence for event in topic_events) / max(len(topic_events), 1),
                2,
            )
            latest = max((event.occurred_at for event in topic_events), default="")
            clusters.append(
                EventCluster(
                    cluster_id=f"C{index:03d}",
                    label=self._cluster_label(topic),
                    summary=self._cluster_summary(topic_events),
                    evidence_ids=list(dict.fromkeys(evidence_ids)),
                    timeline_event_keys=[event.event_key for event in topic_events],
                    sources=sorted(sources),
                    entities=[entity for entity, _ in entities.most_common(5)],
                    confidence=confidence,
                    updated_at=latest,
                )
            )
        return clusters

    def _assess_sources(self, evidence: list[Evidence]) -> list[SourceAssessment]:
        grouped: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            grouped[item.source].append(item)

        assessments: list[SourceAssessment] = []
        for source, items in sorted(grouped.items()):
            best = max(items, key=lambda item: item.reliability)
            is_primary = best.source_type in {"official", "primary", "regulator", "company"} or best.reliability >= 0.9
            notes = []
            if is_primary:
                notes.append("优先信源，可用于确认核心事实。")
            if len(items) >= 2:
                notes.append("该来源提供了多条可复核证据。")
            assessments.append(
                SourceAssessment(
                    source=source,
                    source_type=best.source_type,
                    reliability=round(sum(item.reliability for item in items) / len(items), 2),
                    evidence_count=len(items),
                    is_primary=is_primary,
                    notes=notes or ["普通补充信源。"],
                )
            )
        assessments.sort(key=lambda item: (-item.reliability, item.source))
        return assessments

    def _topic_key(self, item: Evidence) -> str:
        lowered = item.claim.lower()
        if item.claim_type == "business_financing":
            if re.search(r"ipo|public offering|confidential s-1|go public|sec|上市|公开募股", lowered):
                return "business_financing_ipo"
            return "business_financing"
        if item.claim_type == "product_security":
            if re.search(r"lockdown mode|prompt injection|cyberattack|malicious instruction|攻击|注入", lowered):
                return "product_security_lockdown"
            return "product_security"
        if item.claim_type == "casualty":
            if re.search(r"fatal|death|死亡", lowered):
                return "casualty_fatality_status"
            if re.search(r"injur|hospital|surgery|受伤|医院|手术", lowered):
                return "casualty_injury_status"
        if item.claim_type == "rumor_control":
            return "rumor_control"
        if item.claim_type == "cause":
            if re.search(r"investigat|调查", lowered):
                return "cause_investigation"
            if re.search(r"brake|maintenance|制动|维修", lowered):
                return "cause_brake_system"
        if item.claim_type and item.claim_type != "update":
            return item.claim_type
        tokens = [
            token
            for token in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", item.claim.lower())
            if len(token) > 2
        ]
        return "-".join(tokens[:4]) or "update"

    def _date_key(self, value: str) -> str:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
        if match:
            return match.group(1)
        try:
            parsed = parsedate_to_datetime(value or "")
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).date().isoformat()
        except ValueError:
            return value or "unknown-date"

    def _evidence_date_key(self, item: Evidence) -> str:
        date_key = self._date_key(item.published_at)
        if date_key and date_key not in {"1970-01-01", "unknown-date"}:
            return date_key
        extracted = self._extract_date_from_text(f"{item.claim} {item.supporting_text}")
        return extracted or date_key or "unknown-date"

    def _extract_date_from_text(self, text: str) -> str:
        match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
        return ""

    def _confidence(self, items: list[Evidence]) -> float:
        if not items:
            return 0.0
        source_bonus = min(len({item.source for item in items}) * 0.08, 0.24)
        primary_bonus = 0.12 if any(item.reliability >= 0.9 for item in items) else 0.0
        base = sum(item.reliability for item in items) / len(items)
        return round(min(base + source_bonus + primary_bonus, 1.0), 2)

    def _verification_status(self, items: list[Evidence]) -> str:
        sources = {item.source for item in items}
        if len(sources) >= 2 and any(item.reliability >= 0.85 for item in items):
            return "cross_verified"
        if any(item.reliability >= 0.9 for item in items):
            return "primary_confirmed"
        if len(sources) >= 2:
            return "multi_source"
        return "single_source"

    def _event_title(self, topic: str, item: Evidence) -> str:
        labels = {
            "official_update": "官方进展",
            "business_financing": "融资与上市进展",
            "business_financing_ipo": "IPO 进展",
            "product_security": "产品安全进展",
            "product_security_lockdown": "安全模式进展",
            "casualty": "伤亡信息",
            "casualty_fatality_status": "死亡信息核验",
            "casualty_injury_status": "伤者救治进展",
            "cause": "原因调查",
            "cause_investigation": "调查进展",
            "cause_brake_system": "制动系统检查",
            "response": "回应与处置",
            "impact": "影响变化",
            "rumor_control": "谣言澄清",
            "update": "事件更新",
        }
        return labels.get(topic, item.title[:40] or "事件更新")

    def _cluster_label(self, topic: str) -> str:
        return {
            "official_update": "官方通报与权威信息",
            "business_financing": "融资与上市进展",
            "business_financing_ipo": "IPO 与资本市场进展",
            "product_security": "产品安全与风险防护",
            "product_security_lockdown": "安全模式与提示注入防护",
            "casualty": "伤亡与人员影响",
            "casualty_fatality_status": "死亡信息核验",
            "casualty_injury_status": "伤者救治进展",
            "cause": "原因调查与责任线索",
            "cause_investigation": "调查推进",
            "cause_brake_system": "制动系统检查",
            "response": "各方回应与处置",
            "impact": "公共影响与恢复进展",
            "rumor_control": "谣言澄清与信息校正",
            "update": "其他进展",
        }.get(topic, topic)

    def _cluster_summary(self, events: list[TimelineEvent]) -> str:
        if not events:
            return "暂无可归并事件。"
        latest = sorted(events, key=lambda item: item.occurred_at)[-1]
        return f"共归并 {len(events)} 个时间点，最近更新为：{latest.summary}"
