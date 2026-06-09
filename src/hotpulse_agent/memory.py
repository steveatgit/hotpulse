from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re

from .schemas import (
    EventCluster,
    Evidence,
    IncrementalSnapshot,
    ReflectionNote,
    SearchDocument,
    SourceAssessment,
    TimelineEvent,
)


@dataclass
class WorkingMemory:
    current_objective: str = ""
    current_query: str = ""
    last_tool: str = ""
    remaining_steps: int = 0
    query_history: list[str] = field(default_factory=list)


@dataclass
class MemoryManager:
    working: WorkingMemory = field(default_factory=WorkingMemory)
    candidate_docs: list[SearchDocument] = field(default_factory=list)
    fetched_docs: dict[str, SearchDocument] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    reflections: list[ReflectionNote] = field(default_factory=list)
    entities: Counter = field(default_factory=Counter)
    unresolved_questions: list[str] = field(default_factory=list)
    built_timeline: list[TimelineEvent] = field(default_factory=list)
    event_clusters: list[EventCluster] = field(default_factory=list)
    source_assessments: list[SourceAssessment] = field(default_factory=list)
    incremental_snapshot: IncrementalSnapshot | None = None
    archive_loaded: bool = False
    archive_id: str = ""
    previous_evidence_ids: set[str] = field(default_factory=set)
    previous_timeline_signatures: set[str] = field(default_factory=set)
    previous_evidence_count: int = 0
    previous_source_count: int = 0
    previous_timeline_event_count: int = 0

    def add_candidates(self, docs: list[SearchDocument]) -> None:
        seen = {doc.doc_id for doc in self.candidate_docs}
        for doc in docs:
            if doc.doc_id not in seen:
                self.candidate_docs.append(doc)
                seen.add(doc.doc_id)

    def add_fetched(self, doc: SearchDocument) -> None:
        self.fetched_docs[doc.doc_id] = doc
        for entity in doc.entities:
            self.entities[entity] += 1

    def add_evidence(self, items: list[Evidence]) -> None:
        known = {
            (e.doc_id, e.claim)
            for e in self.evidence
        }
        for item in items:
            key = (item.doc_id, item.claim)
            if key not in known:
                if not item.evidence_id:
                    item.evidence_id = f"E{len(self.evidence) + 1:03d}"
                self.evidence.append(item)
                known.add(key)

    def add_reflection(self, category: str, message: str) -> None:
        self.reflections.append(ReflectionNote(category=category, message=message))

    def record_query(self, query: str) -> None:
        if not self.working.query_history or self.working.query_history[-1] != query:
            self.working.query_history.append(query)

    def source_diversity(self) -> int:
        return len({item.source for item in self.evidence})

    def high_reliability_evidence(self, threshold: float = 0.8) -> list[Evidence]:
        return [item for item in self.evidence if item.reliability >= threshold]

    def citation_coverage(self) -> float:
        if not self.evidence:
            return 0.0
        citable = [
            item
            for item in self.evidence
            if item.evidence_id and item.title and item.source and (item.url or item.doc_id)
        ]
        return round(len(citable) / len(self.evidence), 3)

    def primary_source_evidence(self) -> list[Evidence]:
        return [
            item
            for item in self.evidence
            if item.source_type in {"official", "primary", "regulator", "company"}
            or item.reliability >= 0.9
        ]

    def cross_verified_evidence(self) -> list[Evidence]:
        verified: list[Evidence] = []
        for item in self.evidence:
            topic = self._claim_topic(item.claim) or self._claim_signature(item.claim)
            sources = {
                other.source
                for other in self.evidence
                if (self._claim_topic(other.claim) or self._claim_signature(other.claim)) == topic
            }
            if len(sources) >= 2:
                verified.append(item)
        return verified

    def conflict_claims(self) -> list[str]:
        topics: dict[str, set[str]] = {}
        for item in self.evidence:
            topic = self._claim_topic(item.claim)
            sentiment = self._claim_sentiment(item.claim)
            if topic and sentiment:
                topics.setdefault(topic, set()).add(sentiment)

        conflicts = []
        for topic, sentiments in topics.items():
            if {"positive", "negative"}.issubset(sentiments):
                conflicts.append(f"不同来源在“{_topic_zh(topic)}”上存在分歧。")
        return conflicts

    def evidence_coverage(self) -> float:
        score = (
            len(self.high_reliability_evidence()) * 0.16
            + self.source_diversity() * 0.1
            + len(self.cross_verified_evidence()) * 0.08
            + len(self.built_timeline) * 0.04
        )
        return min(score, 1.0)

    def recent_reflection_messages(self, limit: int = 3) -> list[str]:
        return [item.message for item in self.reflections[-limit:]]

    def top_entities(self, limit: int = 4) -> list[str]:
        return [entity for entity, _ in self.entities.most_common(limit)]

    def retrieve_relevant_evidence(self, query: str, limit: int = 3) -> list[Evidence]:
        query_terms = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))
        scored: list[tuple[float, Evidence]] = []
        for item in self.evidence:
            haystack = f"{item.claim} {item.title} {item.source}".lower()
            item_terms = set(re.findall(r"[a-zA-Z0-9]+", haystack))
            overlap = len(query_terms & item_terms)
            score = overlap + item.reliability
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def next_fetch_candidates(self, limit: int = 3) -> list[SearchDocument]:
        unseen = [doc for doc in self.candidate_docs if doc.doc_id not in self.fetched_docs]
        if not unseen:
            return []

        evidence_sources = {item.source for item in self.evidence if item.source}
        grouped: dict[str, list[SearchDocument]] = {}
        for doc in unseen:
            grouped.setdefault(doc.source or doc.doc_id, []).append(doc)
        for docs in grouped.values():
            docs.sort(key=lambda doc: (-doc.reliability, doc.published_at))

        source_order = sorted(
            grouped,
            key=lambda source: (
                source in evidence_sources,
                -max(doc.reliability for doc in grouped[source]),
                source,
            ),
        )

        selected: list[SearchDocument] = []
        for source in source_order:
            selected.append(grouped[source][0])
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            return selected

        selected_ids = {doc.doc_id for doc in selected}
        remaining = [
            doc
            for source in source_order
            for doc in grouped[source][1:]
            if doc.doc_id not in selected_ids
        ]
        return [*selected, *remaining[: limit - len(selected)]]

    def summary(self) -> str:
        top_entities = ", ".join(entity for entity, _ in self.entities.most_common(5))
        return (
            f"候选文档数={len(self.candidate_docs)}，"
            f"已抓取文档数={len(self.fetched_docs)}，"
            f"证据数={len(self.evidence)}，"
            f"来源数={self.source_diversity()}，"
            f"引用覆盖率={self.citation_coverage():.2f}，"
            f"交叉验证证据数={len(self.cross_verified_evidence())}，"
            f"事件簇数={len(self.event_clusters)}，"
            f"历史档案={'已加载' if self.archive_loaded else '未加载'}，"
            f"检索词改写次数={max(len(self.working.query_history) - 1, 0)}，"
            f"高频实体=[{top_entities}]"
        )

    def load_archive_summary(self, archive_id: str, payload: dict) -> None:
        self.archive_id = archive_id
        self.archive_loaded = bool(payload)
        self.previous_evidence_ids = {
            str(item.get("evidence_id", "")).strip()
            for item in payload.get("evidence", [])
            if str(item.get("evidence_id", "")).strip()
        }
        self.previous_timeline_signatures = {
            _timeline_signature_from_mapping(item)
            for item in payload.get("timeline_events", [])
            if _timeline_signature_from_mapping(item)
        }
        latest_snapshot = payload.get("snapshot", {})
        previous_sources = {
            str(item.get("source", "")).strip()
            for item in payload.get("evidence", [])
            if str(item.get("source", "")).strip()
        }
        self.previous_evidence_count = int(latest_snapshot.get("evidence_count", len(self.previous_evidence_ids)) or 0)
        self.previous_source_count = int(latest_snapshot.get("source_count", len(previous_sources)) or 0)
        self.previous_timeline_event_count = int(
            latest_snapshot.get("timeline_event_count", len(self.previous_timeline_signatures)) or 0
        )

    def apply_timeline_result(
        self,
        timeline: list[TimelineEvent],
        clusters: list[EventCluster],
        source_assessments: list[SourceAssessment],
        snapshot: IncrementalSnapshot,
    ) -> None:
        self.built_timeline = timeline
        self.event_clusters = clusters
        self.source_assessments = source_assessments
        snapshot.previous_evidence_count = self.previous_evidence_count
        snapshot.previous_source_count = self.previous_source_count
        snapshot.previous_timeline_event_count = self.previous_timeline_event_count
        snapshot.new_evidence_ids = [
            item.evidence_id
            for item in self.evidence
            if item.evidence_id and item.evidence_id not in self.previous_evidence_ids
        ]
        snapshot.new_timeline_event_keys = [
            item.event_key
            for item in self.built_timeline
            if _timeline_signature(item) not in self.previous_timeline_signatures
        ]
        self.incremental_snapshot = snapshot

    def _claim_signature(self, claim: str) -> str:
        tokens = [
            token
            for token in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", claim.lower())
            if len(token) > 1
        ]
        return "-".join(tokens[:5])

    def _claim_topic(self, claim: str) -> str:
        lowered = claim.lower()
        patterns = {
            "business_financing_ipo": r"ipo|public offering|confidential s-1|go public|sec|上市|公开募股",
            "product_security_lockdown": r"lockdown mode|prompt injection|cyberattack|malicious instruction|攻击|注入",
            "fatalities": r"fatalit",
            "vehicle_speed": r"speed|accelerat",
            "staffing": r"staff",
            "recall_scope": r"recall|distribution region|wider",
            "cause": r"brake|maintenance|temperature|radar",
        }
        for topic, pattern in patterns.items():
            if re.search(pattern, lowered):
                return topic
        return ""

    def _claim_sentiment(self, claim: str) -> str:
        lowered = claim.lower()
        negative_patterns = [
            r"no .+ confirmed",
            r"denied",
            r"had not",
            r"not confirmed",
            r"disputed",
        ]
        positive_patterns = [
            r"confirmed",
            r"started",
            r"opened",
            r"resumed",
            r"remained",
        ]
        if any(re.search(pattern, lowered) for pattern in negative_patterns):
            return "negative"
        if any(re.search(pattern, lowered) for pattern in positive_patterns):
            return "positive"
        return ""


def _topic_zh(topic: str) -> str:
    labels = {
        "fatalities": "死亡人数",
        "vehicle_speed": "车辆速度",
        "staffing": "人员配置",
        "recall_scope": "召回范围",
        "cause": "事故原因",
    }
    return labels.get(topic, topic)


def _timeline_signature(event: TimelineEvent) -> str:
    return "|".join([event.occurred_at, event.title, event.summary])


def _timeline_signature_from_mapping(item: dict) -> str:
    return "|".join(
        [
            str(item.get("occurred_at", "")),
            str(item.get("title", "")),
            str(item.get("summary", "")),
        ]
    ).strip("|")
