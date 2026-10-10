#!/usr/bin/env python3
"""对照 data/names/zh-names.json，列出评委能看到的目录里同一人的不同中文写法。

扫描 web/、pipeline/present/、pipeline/enrich/、inference/、docs/。
只报告，不改这些目录。

同一写法：官方全名、简称、间隔号分开的名或姓，以及句子里粘在译名上的前后文。
不同写法：
- 号码映射（"HOU"/"DAL", 号码）里，这一格的中文不是该球员的官方写法。
  球衣号按原文比对，"00" 不会收成 "0"。同一号码有多人时，中文已经是其中一人的官方写法就不报。
- 引号里的中文，或一句中文的开头、结尾，和某个人的简称或全名只差一个字。
- 只少了间隔号或连字符。
- 替换元组的左边是错写、右边是基准；或左边的姓同时属于另一个人，却被改成其中一人。

    python3 scripts/names/check_names.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAMES = REPO / "data" / "names" / "zh-names.json"
ROSTER = REPO / "data" / "season" / "2025-26" / "priority_team_profiles_reviewed.csv"
ROOTS = (
    REPO / "web",
    REPO / "pipeline" / "present",
    REPO / "pipeline" / "enrich",
    REPO / "inference",
    REPO / "docs",
)
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".m4a", ".mp3", ".wav",
    ".ttf", ".woff", ".woff2", ".otf", ".zip", ".gz", ".pdf", ".pyc",
}
JERSEY_VALUE = re.compile(
    r"""\(\s*["'](HOU|DAL)["']\s*,\s*["'](\d+)["']\s*\)\s*:\s*["']([^"']+)["']"""
)
PAIR = re.compile(r"""\(\s*["']([^"']+)["']\s*,\s*["']([^"']+)["']\s*\)""")
QUOTED = re.compile(r"""["']([\u4e00-\u9fff][^"']{0,30})["']""")
CJK_RUN = re.compile(r"[\u4e00-\u9fff·・.\-－]{2,}")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
TEAM_VARIANTS = (
    "达拉斯小牛队",
    "达拉斯小牛",
    "休斯敦火箭队",
    "休斯敦火箭",
    "小牛队",
    "休斯敦",
    "小牛",
)
LABELS = {
    "display": "展示名",
    "punct": "标点不同",
    "rewrite": "替换源",
    "cross": "替换串人",
}


def compact(text: str) -> str:
    return re.sub(r"[·・.\-－\s]", "", text)


def distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return 99
    return sum(a != b for a, b in zip(left, right))


def load() -> tuple[list[dict], dict[tuple[str, str], list[dict]]]:
    doc = json.loads(NAMES.read_text(encoding="utf-8"))
    players = [_person(row) for row in doc["players"]]
    by_id = {person["player_id"]: person for person in players}
    jerseys: dict[tuple[str, str], list[dict]] = {}
    with ROSTER.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            person = by_id.get(row["player_id"])
            number = (row.get("jersey_number_user_unverified") or "").strip()
            if not person or not number:
                continue
            jerseys.setdefault((row["scope"], number), []).append(person)
    return players, jerseys


def _person(row: dict) -> dict:
    name_zh = row.get("name_zh") or ""
    alias_zh = row.get("alias_zh") or ""
    raw = {name_zh, alias_zh}
    raw.update(part for part in re.split(r"[·・]", name_zh) if part)
    # JD.戴维森、P.J.华盛顿：拉丁字母停住之后的中文也是官方写法。
    # 连字符两边的汉字不要拆开，否则「芬尼-史密斯」会变成另一个「史密斯」。
    for form in (name_zh, alias_zh):
        if re.search(r"[A-Za-z]", form):
            raw.update(re.findall(r"[\u4e00-\u9fff]{2,}", form))
    raw.discard("")
    compacts = {compact(form) for form in raw if compact(form)}
    targets = []
    for form in (alias_zh, name_zh):
        folded = compact(form)
        if len(folded) >= 3 and folded not in targets:
            targets.append(folded)
    return {
        "team": row.get("team") or "",
        "player_id": str(row.get("player_id") or ""),
        "name_en": row.get("name_en") or "",
        "name_zh": name_zh,
        "alias_zh": alias_zh,
        "raw": raw,
        "compacts": compacts,
        "targets": targets,
    }


def files():
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
                continue
            if any(part.startswith(".") for part in path.relative_to(REPO).parts):
                continue
            yield path


def exact_people(token: str, players: list[dict]) -> list[dict]:
    folded = compact(token)
    if not folded:
        return []
    return [person for person in players if folded in person["compacts"]]


def close_to(token: str, person: dict) -> bool:
    """替换元组的左边是不是这个人的错写。森贡和申京等长但两字都不同，也算。"""
    folded = compact(token)
    if not folded:
        return False
    if folded in person["compacts"]:
        return True
    alias = compact(person["alias_zh"])
    full = compact(person["name_zh"])
    for target in person["compacts"]:
        if len(target) < 2:
            continue
        gap = distance(folded, target)
        if gap == 1:
            return True
        if gap == 2 and len(folded) == len(target) and target in {alias, full} and len(target) <= 4:
            return True
    return False


def classify_token(token: str, person: dict) -> str:
    folded = compact(token)
    if not folded or not HAS_CJK.search(token):
        return ""
    if folded in person["compacts"]:
        return "" if token in person["raw"] else "punct"
    return "display"


def snippet(line: str, start: int, length: int) -> str:
    left = max(0, start - 28)
    right = min(len(line), start + length + 28)
    chunk = line[left:right].strip()
    if left > 0:
        chunk = "…" + chunk
    if right < len(line):
        chunk = chunk + "…"
    return chunk


def scan(players: list[dict], jerseys: dict) -> tuple[dict, list]:
    found: dict[tuple, dict] = {}
    teams: list[tuple] = []

    def add(kind: str, person: dict, token: str, rel: str, lineno: int, sample: str, note: str = "") -> None:
        key = (person["team"], person["player_id"], person["name_en"])
        bucket = found.setdefault(key, {"person": person, "rows": []})
        bucket["rows"].append((kind, token, rel, lineno, sample, note))

    for path in files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        rel = path.relative_to(REPO).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            consumed = [False] * len(line)
            _jersey_line(line, rel, lineno, players, jerseys, consumed, add)
            _pair_line(line, rel, lineno, players, consumed, add)
            _quoted_line(line, rel, lineno, players, consumed, add)
            _edge_line(line, rel, lineno, players, consumed, add)
            _team_line(line, rel, lineno, teams)
    return found, teams


def _mark(consumed: list[bool], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(consumed), end)):
        consumed[index] = True


def _jersey_line(line, rel, lineno, players, jerseys, consumed, add) -> None:
    for match in JERSEY_VALUE.finditer(line):
        team, number, token = match.group(1), match.group(2), match.group(3)
        _mark(consumed, match.start(3), match.end(3))
        owners = jerseys.get((team, number), [])
        if len(owners) != 1:
            continue
        person = owners[0]
        kind = classify_token(token, person)
        if not kind:
            continue
        others = [
            other["name_zh"] for other in exact_people(token, players)
            if other["player_id"] != person["player_id"]
        ]
        note = "同时也是" + "、".join(others) + "的官方写法" if others else ""
        add(kind, person, token, rel, lineno, snippet(line, match.start(3), len(token)), note)


def _pair_line(line, rel, lineno, players, consumed, add) -> None:
    for match in PAIR.finditer(line):
        src, dst = match.group(1), match.group(2)
        if not HAS_CJK.search(src):
            continue
        _mark(consumed, match.start(1), match.end(1))
        src_owners = exact_people(src, players)
        dst_owners = exact_people(dst, players)
        sample = snippet(line, match.start(), len(match.group(0)))
        if src_owners and dst_owners:
            src_ids = {person["player_id"] for person in src_owners}
            dst_ids = {person["player_id"] for person in dst_owners}
            extra = [person for person in src_owners if person["player_id"] not in dst_ids]
            if extra and dst_ids < src_ids:
                victim = "、".join(person["name_zh"] for person in dst_owners)
                for person in extra:
                    add(
                        "cross", person, src, rel, lineno, sample,
                        f"「{src}」被换成「{dst}」。{src}也是{victim}的用字，替换后会把{person['name_zh']}的这一截改掉。",
                    )
                continue
            if src_ids == dst_ids and len(src_ids) == 1:
                person = src_owners[0]
                if src not in person["raw"]:
                    add("rewrite", person, src, rel, lineno, sample, f"「{src}」→「{dst}」")
            continue
        if len(dst_owners) == 1 and not src_owners and close_to(src, dst_owners[0]):
            person = dst_owners[0]
            add("rewrite", person, src, rel, lineno, sample, f"「{src}」→「{dst}」")


def _quoted_line(line, rel, lineno, players, consumed, add) -> None:
    for match in QUOTED.finditer(line):
        if consumed[match.start(1):match.end(1)] and all(consumed[match.start(1):match.end(1)]):
            continue
        token = match.group(1).strip()
        if exact_people(token, players):
            kind_people = [
                person for person in exact_people(token, players)
                if token not in person["raw"]
            ]
            if len(kind_people) == 1:
                add("punct", kind_people[0], token, rel, lineno, snippet(line, match.start(1), len(token)))
            continue
        hits = [person for person in players if _fuzzy(token, person)]
        if len(hits) != 1:
            continue
        add("display", hits[0], token, rel, lineno, snippet(line, match.start(1), len(token)))
        _mark(consumed, match.start(1), match.end(1))


def _edge_line(line, rel, lineno, players, consumed, add) -> None:
    blanked = "".join(" " if consumed[i] else ch for i, ch in enumerate(line))
    for match in CJK_RUN.finditer(blanked):
        run = compact(match.group())
        if len(run) < 3:
            continue
        spans = _boundary_spans(run)
        for start, end in spans:
            window = run[start:end]
            if exact_people(window, players):
                continue
            hits = [person for person in players if _fuzzy(window, person)]
            if len(hits) != 1:
                continue
            origin = match.start()
            add("display", hits[0], window, rel, lineno, snippet(line, origin, match.end() - origin))


def _boundary_spans(run: str) -> list[tuple[int, int]]:
    spans = []
    for size in range(3, len(run) + 1):
        spans.append((0, size))
        if size != len(run):
            spans.append((len(run) - size, len(run)))
    return spans


def _fuzzy(token: str, person: dict) -> bool:
    folded = compact(token)
    if len(folded) < 3 or folded in person["compacts"]:
        return False
    return any(distance(folded, target) == 1 for target in person["targets"])


def _team_line(line: str, rel: str, lineno: int, teams: list) -> None:
    occupied = [False] * len(line)
    for variant in TEAM_VARIANTS:
        start = 0
        while True:
            index = line.find(variant, start)
            if index < 0:
                break
            end = index + len(variant)
            if not any(occupied[index:end]):
                for cursor in range(index, end):
                    occupied[cursor] = True
                teams.append((variant, rel, lineno, snippet(line, index, len(variant))))
            start = end


def main() -> int:
    players, jerseys = load()
    found, teams = scan(players, jerseys)
    print("中文译名对照")
    print(f"基准：{NAMES.relative_to(REPO).as_posix()}")
    print("范围：web/、pipeline/present/、pipeline/enrich/、inference/、docs/")
    print("全名、简称、间隔号分开的名或姓，不算另一种写法。号码只核对本格，00 不并进 0。")
    print("这次只报告，不改这些目录里的用字。")
    print()
    if not found and not teams:
        print("没有发现不同写法。")
        return 0
    order = ("display", "punct", "rewrite", "cross")
    titles = {
        "display": "展示名和基准不同",
        "punct": "只差间隔号或连字符",
        "rewrite": "替换规则的源字符串（右边会改成基准，不是最终念出来的字）",
        "cross": "替换规则会把这个人的用字改成另一个人",
    }
    for kind in order:
        blocks = []
        for key in sorted(found):
            bucket = found[key]
            person = bucket["person"]
            rows = []
            seen = set()
            for row_kind, token, rel, lineno, sample, note in bucket["rows"]:
                if row_kind != kind:
                    continue
                mark = (token, rel, lineno, note)
                if mark in seen:
                    continue
                seen.add(mark)
                rows.append((token, rel, lineno, sample, note))
            if rows:
                blocks.append((person, rows))
        if not blocks:
            continue
        print(f"【{titles[kind]}】")
        print()
        for person, rows in blocks:
            print(f"{person['team']} {person['player_id']} {person['name_en']}")
            print(f"  基准：{person['name_zh']}；简称 {person['alias_zh'] or '—'}")
            for token, rel, lineno, sample, note in rows:
                extra = f"（{note}）" if note else ""
                print(f"  {LABELS[kind]} 「{token}」 {rel}:{lineno}{extra}")
                print(f"    {sample}")
            print()
    if teams:
        print("【队名】")
        print("  基准：休斯顿火箭队 / 火箭；达拉斯独行侠队 / 独行侠（china.nba.cn 页面原文）")
        print("  「休斯顿火箭」少一个「队」字，仍算同一个队名，不列在下面。")
        seen = set()
        for variant, rel, lineno, sample in teams:
            mark = (variant, rel, lineno)
            if mark in seen:
                continue
            seen.add(mark)
            print(f"  另一写法 「{variant}」 {rel}:{lineno}")
            print(f"    {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
