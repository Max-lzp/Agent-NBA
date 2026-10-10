"""解说落地：句子里的数字和人名必须来自工具结果。"""

from __future__ import annotations

import json
import re

NUMBER = re.compile(r"\d+(?:\.\d+)?")
CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
LATIN_NAME = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
RATE_WORD = re.compile(r"命中率|xFG|FG%|percent", re.I)

STOP_PHRASES = (
    "预期命中率",
    "三分命中率",
    "两分命中率",
    "命中率",
    "其余两分",
    "中距离",
    "近框",
    "三分",
    "两分",
    "赛季",
    "防守",
    "进攻",
    "助攻",
    "命中",
    "不中",
    "出手",
    "接球",
    "传球",
    "定格",
    "亮点",
    "火箭",
    "独行侠",
    "这一球",
    "这球",
    "这段",
    "回放",
    "片段",
    "数据",
    "没有",
    "查不到",
    "官方",
    "清单",
    "认人",
    "球员",
    "球队",
    "对手",
    "后卫",
    "前锋",
    "中锋",
    "臂展",
    "身高",
    "选秀",
    "落选",
    "厘米",
    "第一节",
    "第二节",
    "第三节",
    "第四节",
    "加时",
    "看点",
    "解说",
    "空位",
    "打铁",
    "进了",
    "没进",
    "工具",
    "足够",
    "回答",
    "问题",
    "正常水平",
    "个百分点",
    "全片",
    "回合",
    "更多",
    "能对上",
    "足够",
    "这个",
    "一次",
    "接到",
    "球了",
)

LATIN_STOP = {
    "the", "this", "that", "and", "with", "from", "made", "missed", "expected",
    "rate", "percent", "shot", "shots", "season", "near", "three", "point",
    "points", "clip", "highlight", "highlights", "freeze", "data", "official",
    "identity", "assist", "defender", "offense", "defense", "rockets", "rocket",
    "mavericks", "maverick", "not", "for", "his", "her", "their", "was", "were",
    "have", "has", "had", "but", "into", "over", "under", "left", "right",
    "make", "miss", "frame", "card", "line", "only", "more", "backed",
    "further", "tools", "enough", "answer", "question", "there", "when",
    "what", "who", "how", "did", "does", "been", "being", "about", "after",
    "before", "during", "game", "clock", "team", "player", "players",
}

FILLER = {
    "zh": "这段没有更多能对上官方或赛季数据的看点。",
    "en": "No further highlight is backed by the official or season data for this clip.",
    "sources": [],
}
NO_ANSWER = {
    "zh": "工具里没有足够的数据来回答这个问题。",
    "en": "The tools do not have enough data to answer that.",
    "sources": [],
}


def _forms(token: str) -> set[str]:
    if re.fullmatch(r"\d+", token):
        return {str(int(token))}
    value = float(token)
    one = f"{value:.1f}".rstrip("0").rstrip(".")
    two = f"{value:.2f}".rstrip("0").rstrip(".")
    return {one, two}


def _add_evidence_number(token: str, allowed: set[str]) -> None:
    allowed |= _forms(token)
    if re.fullmatch(r"\d+\.\d+", token):
        allowed.add(str(int(round(float(token)))))


def collect_numbers(value, allowed: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        _add_evidence_number(str(int(value)) if float(value).is_integer() else str(value), allowed)
        return
    if isinstance(value, str):
        for token in NUMBER.findall(value):
            _add_evidence_number(token, allowed)
        return
    if isinstance(value, dict):
        for item in value.values():
            collect_numbers(item, allowed)
        return
    if isinstance(value, list):
        for item in value:
            collect_numbers(item, allowed)


def collect_pct_numbers(value, allowed: set[str], key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            collect_pct_numbers(child, allowed, str(child_key))
        return
    if isinstance(value, list):
        for child in value:
            collect_pct_numbers(child, allowed, key)
        return
    if value is None or isinstance(value, bool):
        return
    if not (key.endswith("_pct") or key.endswith("_pct_points") or key == "xfg_pct"):
        return
    if isinstance(value, (int, float)):
        _add_evidence_number(str(int(value)) if float(value).is_integer() else str(value), allowed)


def evidence_text(evidence: list[dict]) -> str:
    return "\n".join(json.dumps(item.get("data"), ensure_ascii=False) for item in evidence)


def _numbers_ok(text: str, allowed: set[str]) -> bool:
    for token in NUMBER.findall(text):
        if not (_forms(token) & allowed):
            return False
    return True


def _names_ok(text: str, blob: str) -> bool:
    lowered = blob.lower()
    cleaned = text
    for phrase in STOP_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    for name in CJK.findall(cleaned):
        if name not in blob:
            return False
    for name in LATIN_NAME.findall(text):
        if name.lower() in LATIN_STOP:
            continue
        if name.lower() not in lowered:
            return False
    return True


def _rate_ok(text: str, evidence: list[dict]) -> bool:
    """命中率旁边的数字必须来自百分数字段。出手数里的 0 不能拿来当命中率。"""
    if not RATE_WORD.search(text):
        return True
    numbers = NUMBER.findall(text)
    if not numbers:
        return True
    pct_values: set[str] = set()
    for item in evidence:
        collect_pct_numbers(item.get("data"), pct_values)
    return all(_forms(token) & pct_values for token in numbers)


def sentence_ok(text: str, allowed: set[str], blob: str, evidence: list[dict]) -> bool:
    return _numbers_ok(text, allowed) and _names_ok(text, blob) and _rate_ok(text, evidence)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def ground_text(text: str, evidence: list[dict]) -> str:
    if not text or not str(text).strip():
        return ""
    allowed: set[str] = set()
    for item in evidence:
        collect_numbers(item.get("data"), allowed)
    blob = evidence_text(evidence)
    kept = [part for part in split_sentences(str(text)) if sentence_ok(part, allowed, blob, evidence)]
    return "".join(kept) if any("\u4e00" <= ch <= "\u9fff" for ch in "".join(kept)) else " ".join(kept)


def sources_for(text: str, model_sources, evidence: list[dict]) -> list[str]:
    known = []
    for item in evidence:
        source = item.get("source")
        if isinstance(source, str) and source and source not in known:
            known.append(source)
    picked = []
    if isinstance(model_sources, list):
        for source in model_sources:
            if isinstance(source, str) and source in known and source not in picked:
                picked.append(source)
    if picked or not str(text).strip():
        return picked
    numbers = NUMBER.findall(text)
    cleaned = text
    for phrase in STOP_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    names = CJK.findall(cleaned)
    names += [name for name in LATIN_NAME.findall(text) if name.lower() not in LATIN_STOP]
    for item in evidence:
        source = item.get("source")
        if not source or source in picked:
            continue
        values: set[str] = set()
        collect_numbers(item.get("data"), values)
        if numbers and not all(_forms(token) & values for token in numbers):
            continue
        blob = json.dumps(item.get("data"), ensure_ascii=False)
        if names and not all(name.lower() in blob.lower() for name in names):
            continue
        if numbers or names:
            picked.append(source)
    return picked
