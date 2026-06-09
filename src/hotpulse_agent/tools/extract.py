from __future__ import annotations

import re

from ..schemas import Evidence, SearchDocument
from .base import BaseTool


class ExtractEvidenceTool(BaseTool):
    name = "extract_evidence"
    BOILERPLATE_PATTERNS = [
        r"\bcookies?\b",
        r"\btracker preferences?\b",
        r"\bprivacy choices?\b",
        r"\bsubscriber agreement\b",
        r"\bdistribution and use of this material\b",
        r"\bcopyright law\b",
        r"\bdow jones reprints\b",
        r"\bwhat to read next\b",
        r"\bskip to main content\b",
        r"\bmost popular news\b",
        r"\bmost popular opinion\b",
        r"\bsale[\"”]? or [\"“]?sharing\b",
        r"\bdeliver ads\b",
        r"\bmanage your tracker\b",
        r"\bopt in or out\b",
        r"\bfully opt out\b",
        r"\bprivacy policy\b",
        r"\bnot subscribed\b",
        r"\bsign up here\b",
        r"\bread in app\b",
        r"\bsavesaved\b",
        r"\bloading audio narration\b",
        r"\bbecome a subscriber\b",
        r"\bavailable exclusively to .+ subscribers\b",
        r"\bsign up for .+ newsletter\b",
        r"\bdaily pitch newsletter\b",
        r"\byour rights\b",
        r"\.(jpg|jpeg|png|gif|webp)\b",
        r"\bimage\sbq\b",
        r"\bthe lobby of\b",
        r"\btarget url returned error\b",
        r"\b403:\s*forbidden\b",
        r"\brequiring captcha\b",
        r"\bjust a moment\b",
        r"\bcloudflare\b",
        r"^\$?\s*\d+[\d\s,.]*\s*(soum|eur|usd|₽|¥)",
        r"^(?:\d{3,}(?:\.\d+)?\\?\s*){3,}",
    ]

    def run(self, doc: SearchDocument) -> list[Evidence]:
        if self._is_failed_fetch_content(doc.content):
            return []
        claims = doc.claims or self._derive_claims(doc.content)
        if not doc.claims:
            relevant_claims = [claim for claim in claims if self._is_relevant_to_title(doc.title, claim)]
            claims = relevant_claims or claims[:1]
        evidence = []
        for index, claim in enumerate(claims, start=1):
            evidence.append(Evidence(
                doc_id=doc.doc_id,
                title=doc.title,
                source=doc.source,
                source_type=doc.source_type,
                published_at=doc.published_at,
                claim=claim,
                supporting_text=self._supporting_text(doc.content, claim),
                reliability=doc.reliability,
                evidence_id=f"{doc.doc_id}#{index}",
                url=doc.url,
                entities=list(doc.entities),
                tags=list(doc.tags),
                claim_type=self._claim_type(claim),
            ))
        return evidence

    def _derive_claims(self, content: str, limit: int = 3) -> list[str]:
        cleaned = self._clean_content(content)
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        scored: list[tuple[int, str]] = []
        for sentence in sentences:
            sentence = self._clean_sentence(sentence)
            if len(sentence) < 40:
                continue
            if self._is_boilerplate(sentence):
                continue
            score = self._claim_score(sentence)
            if score <= 0:
                continue
            scored.append((score, sentence[:220].rstrip()))
        scored.sort(key=lambda item: (-item[0], len(item[1])))
        claims = []
        seen: set[str] = set()
        for _, sentence in scored:
            signature = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", sentence.lower()).strip()[:80]
            if signature in seen:
                continue
            claims.append(sentence)
            seen.add(signature)
            if len(claims) >= limit:
                break
        return claims

    def _is_boilerplate(self, sentence: str) -> bool:
        lowered = sentence.lower()
        return any(re.search(pattern, lowered) for pattern in self.BOILERPLATE_PATTERNS)

    def _is_failed_fetch_content(self, content: str) -> bool:
        lowered = (content or "").lower()
        failure_markers = [
            "target url returned error",
            "403: forbidden",
            "requiring captcha",
            "just a moment",
        ]
        return any(marker in lowered for marker in failure_markers)

    def _clean_sentence(self, sentence: str) -> str:
        sentence = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", sentence)
        sentence = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", sentence)
        sentence = re.sub(r"https?://\S+", "", sentence)
        sentence = re.sub(r"\b[A-Za-z0-9+/]{60,}={0,2}\b", "", sentence)
        sentence = re.sub(r"#+\s*", "", sentence)
        return re.sub(r"\s+", " ", sentence).strip()

    def _clean_content(self, content: str) -> str:
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", content or "")
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"`{1,3}[^`]+`{1,3}", " ", text)
        text = re.sub(r"\b[A-Za-z0-9+/]{80,}={0,2}\b", " ", text)
        lines = []
        for line in text.splitlines():
            cleaned = self._clean_sentence(line)
            if not cleaned or self._is_boilerplate(cleaned):
                continue
            if len(cleaned) < 20 and not re.search(r"\d{4}|confirmed|announced|said|filed|launched", cleaned.lower()):
                continue
            lines.append(cleaned)
        return re.sub(r"\s+", " ", " ".join(lines)).strip()

    def _claim_score(self, sentence: str) -> int:
        lowered = sentence.lower()
        score = 0
        strong_patterns = [
            r"\bconfirmed\b",
            r"\bannounced\b",
            r"\bsaid\b",
            r"\bfiled\b",
            r"\blaunched\b",
            r"\breported\b",
            r"\bdenied\b",
            r"\binvestigation\b",
            r"\baccording to\b",
            r"\bipo\b",
            r"\bsec\b",
            r"\bcyberattack",
            r"\bprompt injection\b",
            r"确认|宣布|表示|通报|否认|调查|提交|上市|攻击|注入",
        ]
        weak_patterns = [
            r"\bopenai\b",
            r"\bchatgpt\b",
            r"\bsam altman\b",
            r"\b202[0-9]\b",
        ]
        score += sum(3 for pattern in strong_patterns if re.search(pattern, lowered))
        score += sum(1 for pattern in weak_patterns if re.search(pattern, lowered))
        if sentence.count("[") + sentence.count("]") > 4:
            score -= 3
        if re.search(r"jpg|jpeg|png|gif|newsletter|subscribe|skip to main|distribution and use", lowered):
            score -= 5
        if len(re.findall(r"\b[A-Z][a-z]+\b", sentence)) >= 2:
            score += 1
        return score

    def _is_relevant_to_title(self, title: str, claim: str) -> bool:
        title_terms = self._meaningful_terms(title)
        claim_terms = self._meaningful_terms(claim)
        if title_terms & claim_terms:
            return True
        claim_type = self._claim_type(claim)
        if claim_type in {"business_financing", "product_security", "rumor_control"}:
            return True
        return False

    def _meaningful_terms(self, text: str) -> set[str]:
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "says",
            "said",
            "news",
            "tech",
            "open",
            "public",
            "going",
            "files",
            "filed",
            "ai",
        }
        return {
            token
            for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-]+", text.lower())
            if len(token) > 2 and token not in stopwords
        }

    def _supporting_text(self, content: str, claim: str, max_chars: int = 260) -> str:
        cleaned = self._clean_content(content)
        if not cleaned:
            return claim[:max_chars]
        anchor = claim[:80].strip()
        index = cleaned.find(anchor) if anchor else -1
        if index < 0:
            return cleaned[:max_chars]
        start = max(index - 40, 0)
        return self._clean_sentence(cleaned[start : start + max_chars]).strip()

    def _claim_type(self, claim: str) -> str:
        lowered = claim.lower()
        patterns = {
            "rumor_control": r"rumou?r|disinformation|misinformation|谣言|传言|辟谣",
            "business_financing": r"ipo|public offering|confidential s-1|go public|sec|上市|招股|公开募股",
            "product_security": r"lockdown mode|cyberattack|prompt injection|malicious instruction|安全|攻击|注入",
            "casualty": r"fatal|death|injur|casualt|hospital|surgery|alive|死亡|伤亡|受伤|医院|手术|生还",
            "cause": r"cause|investigat|brake|maintenance|原因|调查|故障",
            "response": r"response|announc|statement|回应|声明|处置",
            "impact": r"impact|closed|resumed|delay|影响|关闭|恢复|延误",
            "official_update": r"official|authority|police|agency|regulator|通报|官方|部门",
        }
        for claim_type, pattern in patterns.items():
            if re.search(pattern, lowered):
                return claim_type
        return "update"
