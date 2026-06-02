from __future__ import annotations

import re


_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def prefer_chinese(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text or ""))


def preferred_language(text: str) -> str:
    return "zh" if prefer_chinese(text) else "en"


def extract_chinese_terms(text: str, limit: int = 6) -> list[str]:
    stop_phrases = {
        "梳理",
        "过去",
        "关键进展",
        "冲突点",
        "关键信源",
        "最新进展",
        "热点",
        "事件",
        "问题",
        "情况",
    }
    seen: set[str] = set()
    terms: list[str] = []
    for item in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        token = item.strip()
        if not token or token in stop_phrases or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def extract_latin_terms(text: str, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for item in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", text or ""):
        token = item.strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def extract_chinese_search_hints(text: str) -> list[str]:
    hints: list[str] = []
    rules = [
        ("24小时", "过去24小时"),
        ("最新", "最新进展"),
        ("进展", "最新进展"),
        ("冲突", "冲突说法"),
        ("说法", "冲突说法"),
        ("信源", "关键信源"),
        ("官方", "官方通报"),
        ("调查", "调查进展"),
    ]
    for trigger, label in rules:
        if trigger in (text or "") and label not in hints:
            hints.append(label)
    return hints