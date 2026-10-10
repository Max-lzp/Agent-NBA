"""解说落地：句子里的数字和人名必须来自工具结果。"""

from __future__ import annotations

import json
import re

import season_data

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
    "盖帽",
    "犯规",
    "比分",
    "近框",
    "三区",
    "距离",
    "年薪",
    "工资",
    "薪水",
    "下一场",
    "防守人",
    "客场",
    "主场",
    "被盖",
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

# 这些字不是人名。从句子里拿掉之后，剩下的汉字才拿去对球员。
# 这些字不是人名。译名先整段拿掉，剩下的单字再抹掉，避免「球传」粘成一个假名字。
PARTICLES = set(
    "在里的了和与并把被也都就还这那个是有从到给对让又而但或及其中上下后前时呢吗吧啊会能来去过着向出"
    "球传投进得分次个名位号秒板帽断手脚篮框空手跑跳站挡切拿送扔卡做往"
)
FUNCTION_WORDS = (
    "什么", "怎么", "这个", "一个", "没有", "不是", "而是", "然后", "所以", "因为",
    "但是", "还是", "就是", "已经", "可以", "他们", "自己", "这里", "里面", "之后",
    "之前", "一下", "一直", "出现", "特别", "动作", "没有", "比赛", "回放", "这段",
)
ACTION_ZH = {
    "receive": "接到球",
    "hold": "持球",
    "rebound": "抢到篮板",
    "turnover": "失误",
    "foul": "犯规",
    "steal": "抢断",
    "block": "盖帽",
}
ACTION_EN = {
    "接到球": "receives the ball",
    "持球": "holds the ball",
    "传球": "passes the ball",
    "出手命中": "makes the shot",
    "出手没进": "misses the shot",
    "出手": "takes a shot",
    "抢到篮板": "grabs a rebound",
    "失误": "turns the ball over",
    "犯规": "commits a foul",
    "抢断": "steals the ball",
    "盖帽": "blocks the shot",
    "助攻": "records an assist",
}

LATIN_CHUNK = re.compile(
    r"\b(?:[A-Z]\.\s*){0,3}[A-Z][A-Za-z'.-]*(?:\s+(?:[A-Z]\.\s*){0,3}[A-Z][A-Za-z'.-]*)*"
)
_EMPTY_NOTE = re.compile(r"没有更多|没有足够|查不到|no further|not enough|do not have enough", re.I)

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


def _people_in_evidence(evidence: list[dict]) -> list[dict]:
    """先吃掉更长的译名，避免「芬尼-史密斯」被当成另一个史密斯。"""
    blob = evidence_text(evidence)
    found: list[dict] = []
    seen: set[str] = set()

    def add(person) -> None:
        if not person:
            return
        key = person["name_en"]
        if key in seen:
            return
        seen.add(key)
        found.append(person)

    masked = blob
    ranked = []
    for person in season_data.roster():
        for form in person["forms"]:
            if form:
                ranked.append((len(form), form, person))
    for _, form, person in sorted(ranked, key=lambda item: item[0], reverse=True):
        if form in masked:
            add(person)
            masked = masked.replace(form, " " * len(form))
    for span in CJK.findall(blob):
        if span in STOP_PHRASES or span in FUNCTION_WORDS:
            continue
        for person in season_data.roster():
            alias = person["alias_zh"]
            if alias and len(span) == len(alias) and season_data._hamming(span, alias) == 1:
                add(person)
    for match in LATIN_CHUNK.finditer(blob):
        chunk = match.group(0).strip()
        if len(chunk) >= 2:
            add(season_data.match_name(chunk))
    return found


def _rewrite_near_names(text: str, people: list[dict]) -> str:
    """差一个字的译名改成名单上的简称，例如马歇尔写成马绍尔。"""
    if not text or not people:
        return text or ""
    aliases = sorted(
        {person["alias_zh"] for person in people if len(person.get("alias_zh") or "") >= 2},
        key=len,
        reverse=True,
    )

    def rewrite_span(span: str) -> str:
        pieces = []
        index = 0
        while index < len(span):
            replaced = False
            if index == 0 or span[index - 1] in PARTICLES:
                for alias in aliases:
                    size = len(alias)
                    window = span[index:index + size]
                    if len(window) != size or window in STOP_PHRASES or window in FUNCTION_WORDS:
                        continue
                    if season_data._hamming(window, alias) == 1:
                        pieces.append(alias)
                        index += size
                        replaced = True
                        break
            if not replaced:
                pieces.append(span[index])
                index += 1
        return "".join(pieces)

    return CJK.sub(lambda match: rewrite_span(match.group(0)), text)


def _strip_known_zh(text: str, people: list[dict]) -> str:
    cleaned = text
    forms = []
    for person in people:
        forms.extend(person["forms"])
    for form in sorted(set(forms), key=len, reverse=True):
        if form:
            cleaned = cleaned.replace(form, " ")
    for phrase in sorted(STOP_PHRASES + FUNCTION_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(phrase, " ")
    cleaned = _rewrite_near_names(cleaned, people)
    for person in people:
        alias = person["alias_zh"]
        if alias:
            cleaned = cleaned.replace(alias, " ")
    cleaned = "".join(" " if ch in PARTICLES else ch for ch in cleaned)
    return cleaned


def _names_ok(text: str, blob: str, people: list[dict]) -> bool:
    lowered = blob.lower()
    cleaned = _strip_known_zh(text, people)
    if CJK.findall(cleaned):
        return False
    for name in LATIN_NAME.findall(text):
        if name.lower() in LATIN_STOP:
            continue
        if name.lower() not in lowered:
            return False
    return True


def _rate_numbers(text: str) -> list[str]:
    """只核对紧挨着命中率的那个数字。同一句里的出手数不算命中率。"""
    found = []
    for match in RATE_WORD.finditer(text):
        after = text[match.end():match.end() + 16]
        before = text[max(0, match.start() - 8):match.start()]
        tokens = NUMBER.findall(after) or NUMBER.findall(before)
        if tokens:
            found.append(tokens[0])
    return found


def _rate_ok(text: str, evidence: list[dict]) -> bool:
    """命中率旁边的数字必须来自百分数字段。出手数里的 0 不能拿来当命中率。"""
    numbers = _rate_numbers(text)
    if not numbers:
        return True
    pct_values: set[str] = set()
    for item in evidence:
        collect_pct_numbers(item.get("data"), pct_values)
    return all(_forms(token) & pct_values for token in numbers)


def sentence_ok(text: str, allowed: set[str], blob: str, evidence: list[dict], people: list[dict]) -> bool:
    return _numbers_ok(text, allowed) and _names_ok(text, blob, people) and _rate_ok(text, evidence)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s*|\n+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _name_blob(evidence: list[dict], people: list[dict]) -> str:
    extra = []
    for person in people:
        extra.extend(person["forms"])
        extra.append(person["name_en"])
    return evidence_text(evidence) + "\n" + " ".join(extra)


def ground_text(text: str, evidence: list[dict]) -> str:
    if not text or not str(text).strip():
        return ""
    people = _people_in_evidence(evidence)
    text = _rewrite_near_names(str(text), people)
    allowed: set[str] = set()
    for item in evidence:
        collect_numbers(item.get("data"), allowed)
    blob = _name_blob(evidence, people)
    kept = [part for part in split_sentences(text) if sentence_ok(part, allowed, blob, evidence, people)]
    return "".join(kept) if any("\u4e00" <= ch <= "\u9fff" for ch in "".join(kept)) else " ".join(kept)


def _mentioned(text: str, people: list[dict]) -> list[dict]:
    if not text or not people:
        return []
    rewritten = _rewrite_near_names(text, people)
    masked = rewritten
    found = []
    seen: set[str] = set()
    allowed = {person["name_en"]: person for person in people}
    ranked = []
    for person in people:
        for form in person["forms"]:
            if form:
                ranked.append((len(form), form, person))
    for _, form, person in sorted(ranked, key=lambda item: item[0], reverse=True):
        if form in masked:
            masked = masked.replace(form, " " * len(form))
            if person["name_en"] not in seen:
                seen.add(person["name_en"])
                found.append(person)
    for match in LATIN_CHUNK.finditer(text):
        hit = season_data.match_name(match.group(0))
        if hit and hit["name_en"] in allowed and hit["name_en"] not in seen:
            seen.add(hit["name_en"])
            found.append(allowed[hit["name_en"]])
    lowered = text.lower()
    for person in people:
        if person["name_en"] in seen:
            continue
        last = person["last"]
        same = [item for item in people if item["last"] == last]
        if len(same) == 1 and last and re.search(rf"\b{re.escape(last)}\b", lowered):
            seen.add(person["name_en"])
            found.append(person)
    return found


def _person_text(node) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    bits = []
    for key in ("name", "player", "player_name", "name_zh", "name_en"):
        if node.get(key):
            bits.append(str(node[key]))
    return " ".join(bits)


def _side_text(node: dict, key: str) -> str:
    child = node.get(key)
    if isinstance(child, dict):
        return _person_text(child)
    if isinstance(child, str):
        return child
    return ""


def _actions_for(person: dict, evidence: list[dict]) -> list[str]:
    actions = []

    def consider(name_text: str, phrase: str) -> None:
        if phrase and name_text and phrase not in actions and _mentioned(name_text, [person]):
            actions.append(phrase)

    def walk(value) -> None:
        if isinstance(value, dict):
            kind = str(value.get("type") or value.get("kind") or "").lower()
            if kind:
                actor = _side_text(value, "actor") or _person_text(value)
                if kind == "pass":
                    consider(actor, "传球")
                    consider(_side_text(value, "target"), "接到球")
                else:
                    consider(actor, _action_phrase(kind, value))
                assist = value.get("assist")
                official = value.get("official") if isinstance(value.get("official"), dict) else {}
                if not assist:
                    assist = official.get("assist")
                if isinstance(assist, dict):
                    consider(_person_text(assist), "助攻")
                elif isinstance(assist, str):
                    consider(assist, "助攻")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for item in evidence:
        walk(item.get("data"))
    return actions[:4]


def _action_phrase(kind: str, node: dict) -> str:
    if kind == "pass":
        return "传球"
    if kind == "shot":
        made = node.get("made")
        official = node.get("official") if isinstance(node.get("official"), dict) else {}
        if made is None:
            made = official.get("made")
        if made is True:
            return "出手命中"
        if made is False:
            return "出手没进"
        return "出手"
    return ACTION_ZH.get(kind, "")


def _chinese_from_context(en: str, evidence: list[dict], question: str) -> str:
    people = _people_in_evidence(evidence)
    asked = _mentioned(f"{question}\n{en}", people)
    lines = []
    weak = []
    for person in asked:
        actions = _actions_for(person, evidence)
        alias = person["alias_zh"]
        if actions:
            lines.append(f"{alias}在这段里{'，'.join(actions)}。")
        else:
            weak.append(f"这段里能对上{alias}。")
    if lines:
        return "".join(lines)
    if weak:
        return weak[0]
    match = re.search(r"(\d+(?:\.\d+)?)\s*percent", en or "", re.I)
    if match:
        label = "预期命中率" if re.search(r"expected", en or "", re.I) else "命中率"
        return f"{label}是 {match.group(1)}%。"
    return ""


def _english_from_context(zh: str, evidence: list[dict]) -> str:
    people = _people_in_evidence(evidence)
    asked = _mentioned(zh, people)
    lines = []
    for person in asked:
        actions = _actions_for(person, evidence)
        verbs = [ACTION_EN[item] for item in actions if item in ACTION_EN]
        if verbs:
            lines.append(f"{person['name_en']} {' and '.join(verbs)} in this clip.")
        else:
            lines.append(f"{person['name_en']} is in this clip.")
    return " ".join(lines)


def _empty_note(text: str, evidence: list[dict]) -> bool:
    if not text or NUMBER.search(text) or not _EMPTY_NOTE.search(text):
        return False
    return not _mentioned(text, _people_in_evidence(evidence))


def fill_pair(zh: str, en: str, evidence: list[dict], *, question: str = "", require_zh: bool = False) -> tuple[str, str]:
    """两栏都尽量留下。中文问题至少要有中文，人名用统一译名。"""
    zh_text = ground_text(zh, evidence)
    en_text = ground_text(en, evidence)
    if not zh_text and (en_text or require_zh):
        zh_text = ground_text(_chinese_from_context(en_text, evidence, question), evidence)
    if not en_text and zh_text:
        en_text = ground_text(_english_from_context(zh_text, evidence), evidence)
    if zh_text and not en_text and _empty_note(zh_text, evidence):
        en_text = FILLER["en"]
    if en_text and not zh_text and _empty_note(en_text, evidence):
        zh_text = FILLER["zh"]
    return zh_text, en_text


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
    for item in evidence:
        source = item.get("source")
        if not source or source in picked:
            continue
        values: set[str] = set()
        collect_numbers(item.get("data"), values)
        if numbers and not all(_forms(token) & values for token in numbers):
            continue
        blob = json.dumps(item.get("data"), ensure_ascii=False)
        people = _people_in_evidence([item])
        if not _names_ok(text, _name_blob([item], people), people):
            continue
        if numbers or people or LATIN_NAME.findall(text):
            picked.append(source)
    return picked
