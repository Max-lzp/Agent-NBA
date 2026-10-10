"""逐题评测 DeepCourtAnalyst。

线上：POST /api/agent/ask。模拟：用考试片形状的假媒体桶直接调 agent.invoke。
参考答案只在 questions.json 里，用来打分。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

import agent  # noqa: E402
import converse_loop  # noqa: E402
import season_data  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.json"
FIXTURE_PATH = EVAL_DIR / "fixtures" / "exam_media.json"
REPORT_PATH = EVAL_DIR / "report.md"
ASK_URL = "https://d2vzd2batw1eqe.cloudfront.net/api/agent/ask"
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TEAM_ZH = {"HOU": "火箭", "DAL": "独行侠"}
ZONE_ZH = (
    ("near_six_ft", "命中率", "make rate"),
    ("other_two_point_ge_six_ft", "其余两分命中率", "other two-point percentage"),
    ("three_point", "三分命中率", "three-point percentage"),
)


class FakeBody:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        body = self.objects[Key]
        raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body, ensure_ascii=False).encode()
        return {"Body": FakeBody(raw)}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix") or ""
        keys = [key for key in self.objects if key.startswith(prefix)]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}


def load_questions() -> dict:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))


def _payloads(messages) -> list[dict]:
    found = []
    for message in messages:
        for block in message.get("content") or []:
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not isinstance(result, dict):
                continue
            for part in result.get("content") or []:
                text = part.get("text") if isinstance(part, dict) else ""
                try:
                    data = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict):
                    found.append(data)
    return found


def _uses(messages) -> list[tuple[str, dict]]:
    found = []
    for message in messages:
        for block in message.get("content") or []:
            use = block.get("toolUse") if isinstance(block, dict) else None
            if isinstance(use, dict):
                found.append((str(use.get("name") or ""), use.get("input") or {}))
    return found


def _tools(calls) -> dict:
    return {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": f"e{index}", "name": name, "input": payload}}
            for index, (name, payload) in enumerate(calls)
        ],
    }


def _game_id(results: list[dict]) -> str:
    for item in results:
        for key in ("gameId", "game_id"):
            if item.get(key):
                return str(item[key])
    return ""


def _season_queries(question: str) -> list[dict]:
    queries = []
    seen = set()
    masked = question

    def add(name: str, team: str) -> None:
        key = (name, team)
        if key in seen or not name:
            return
        seen.add(key)
        queries.append({"name": name, "team": team})

    ranked = []
    for person in season_data.roster():
        for form in (person["name_en"], person["name_zh"], person["alias_zh"]):
            if form:
                ranked.append((len(form), form, person))
    for _, form, person in sorted(ranked, key=lambda item: item[0], reverse=True):
        if form in masked:
            add(person["name_en"], person["team"])
            masked = masked.replace(form, " ")
    for label, team in (
        ("火箭", "HOU"),
        ("独行侠", "DAL"),
        ("Rockets", "HOU"),
        ("Mavericks", "DAL"),
        ("Houston", "HOU"),
        ("Dallas", "DAL"),
    ):
        if label in question:
            add(team, team)
    return queries


def _fmt(value) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(round(number, 1))


def _shots(results: list[dict]) -> list[dict]:
    rows = []
    seen = set()
    for item in results:
        for poss in item.get("possessions") or []:
            if not isinstance(poss, dict):
                continue
            for event in poss.get("events") or []:
                if isinstance(event, dict) and event.get("type") == "shot":
                    _add_shot(rows, seen, event, official=True)
        for play in item.get("plays") or []:
            if isinstance(play, dict) and play.get("kind") == "shot":
                _add_shot(rows, seen, play, official=False)
    return rows


def _add_shot(rows, seen, node, *, official: bool) -> None:
    body = node.get("official") if official and isinstance(node.get("official"), dict) else node
    actor = node.get("actor") if isinstance(node.get("actor"), dict) else node
    name = actor.get("name_zh") or actor.get("name") or node.get("name_zh") or node.get("player") or ""
    name_en = actor.get("name_en") or node.get("name_en") or ""
    description = str(body.get("description") or node.get("description") or "")
    made = body.get("made") if isinstance(body, dict) else None
    if made is None:
        made = node.get("made")
    value = body.get("value") if isinstance(body, dict) else None
    if value is None:
        value = node.get("value")
    xfg = None
    if isinstance(body, dict):
        xfg = body.get("xfg_pct")
    if xfg is None:
        xfg = node.get("xfg_pct")
    key = description or (name, made, value, xfg)
    row = {
        "name": name,
        "name_en": name_en,
        "made": made,
        "value": value,
        "xfg": xfg,
        "description": description,
        "score_away": node.get("score_away"),
        "score_home": node.get("score_home"),
        "clock_s": node.get("clock_s"),
        "_key": key,
    }
    if key in seen:
        for existing in rows:
            if existing.get("_key") != key:
                continue
            for field in ("score_away", "score_home", "xfg", "name_en", "clock_s", "name"):
                if existing.get(field) in (None, "") and row.get(field) not in (None, ""):
                    existing[field] = row[field]
        return
    seen.add(key)
    rows.append(row)


def _people_from_contacts(results: list[dict]) -> list[str]:
    names = []
    for item in results:
        for play in item.get("plays") or []:
            if not isinstance(play, dict):
                continue
            if play.get("kind") in {"block", "foul"}:
                names.append(play.get("name_zh") or play.get("name_en") or play.get("player") or "")
            for key in ("blocked_by_zh", "fouled_by_zh"):
                if play.get(key):
                    names.append(str(play[key]))
    cleaned = []
    for name in names:
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def _players(results: list[dict]) -> list[dict]:
    return [
        item for item in results
        if item.get("found") and item.get("player_name") and isinstance(item.get("offense"), dict)
    ]


def _teams(results: list[dict]) -> list[dict]:
    return [
        item for item in results
        if item.get("found") and item.get("team") and not item.get("player_name") and isinstance(item.get("offense"), dict)
    ]


def _finals(results: list[dict]) -> list[dict]:
    found = []
    for item in results:
        final = item.get("final")
        if isinstance(final, dict) and final.get("away_score") is not None:
            found.append(final)
    return found


def _sources(results: list[dict]) -> list[str]:
    found = []
    for item in results:
        source = item.get("source")
        if isinstance(source, str) and source and source not in found:
            found.append(source)
    return found


def _pair(zh: str, en: str, zh_lines: list[str], en_lines: list[str]) -> None:
    if zh:
        zh_lines.append(zh if zh.endswith("。") else zh + "。")
    if en:
        en_lines.append(en if en.endswith(".") else en + ".")


def _missing(zh_lines, en_lines) -> None:
    _pair("数据里没有", "The tools do not have that", zh_lines, en_lines)


def draft(question: str, results: list[dict]) -> str:
    intents = _intents(question)
    zh_lines: list[str] = []
    en_lines: list[str] = []
    shots = _shots(results)
    if "wingspan" in intents:
        _say_wingspan(question, results, zh_lines, en_lines)
    elif "salary" in intents or "next" in intents or "distance" in intents:
        _missing(zh_lines, en_lines)
    else:
        if "shooter" in intents or "result" in intents:
            _say_shots(shots, zh_lines, en_lines, with_result="result" in intents or "shooter" in intents)
        if "xfg" in intents:
            _say_xfg(shots, zh_lines, en_lines)
        if "defender" in intents:
            _say_defenders(results, zh_lines, en_lines)
        if "season3" in intents:
            _say_season_three(results, zh_lines, en_lines)
        if "defense" in intents:
            _say_defense(results, zh_lines, en_lines)
        if "team_off" in intents:
            _say_team(results, "offense", zh_lines, en_lines)
        if "team_def" in intents:
            _say_team(results, "defense", zh_lines, en_lines)
        if "score" in intents:
            _say_score(shots, results, zh_lines, en_lines)
        if "final" in intents:
            _say_final(results, zh_lines, en_lines)
    if not zh_lines and not en_lines:
        _missing(zh_lines, en_lines)
    payload = {
        "answer": {
            "zh": "".join(zh_lines),
            "en": " ".join(en_lines),
            "sources": _sources(results),
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _intents(question: str) -> set[str]:
    text = question.lower()
    found = set()
    if any(token in question for token in ("臂展",)) or "wingspan" in text:
        found.add("wingspan")
    if any(token in question for token in ("年薪", "工资", "薪水")) or "salary" in text:
        found.add("salary")
    if "下一场" in question or "next game" in text:
        found.add("next")
    if any(token in question for token in ("防守距离", "防守人距离")) or "closest defender" in text or "defender distance" in text:
        found.add("distance")
    if found & {"wingspan", "salary", "next", "distance"}:
        return found
    if any(token in question for token in ("谁出手", "谁投的", "是谁出手")) or "who shot" in text:
        found.add("shooter")
    if any(token in question for token in ("结果", "不中", "进了还是")) or "result" in text:
        found.add("result")
    if any(token in question for token in ("谁在防", "谁防", "防守出手")) or "who was the defender" in text or text.startswith("who was the defender"):
        found.add("defender")
    if "预期" in question or "expected" in text:
        found.add("xfg")
    if any(token in question for token in ("三分命中率", "赛季三分")) or "three-point percentage" in text:
        found.add("season3")
    if "防守数据" in question or "defensive shooting" in text or ("防守" in question and "赛季" in question and "三区" not in question):
        found.add("defense")
    if "三区" in question and any(token in question for token in ("火箭", "独行侠", "两队")):
        if "进攻" in question:
            found.add("team_off")
        if "防守" in question:
            found.add("team_def")
    if "比分" in question or "score" in text:
        if any(token in question for token in ("最终", "全场")) or "final" in text:
            found.add("final")
        else:
            found.add("score")
    if "最终比分" in question:
        found.add("final")
    return found


def _say_shots(shots, zh_lines, en_lines, *, with_result: bool) -> None:
    if not shots:
        _missing(zh_lines, en_lines)
        return
    seen = set()
    for shot in shots:
        name = shot["name"] or shot["name_en"]
        if not name or name in seen and not with_result:
            continue
        seen.add(name)
        kind = "三分" if shot["value"] == 3 else "两分" if shot["value"] == 2 else ""
        if shot["made"] is True:
            _pair(f"{name}{kind}出手命中", f"{shot['name_en'] or name} makes the shot", zh_lines, en_lines)
        elif shot["made"] is False:
            _pair(f"{name}{kind}出手不中", f"{shot['name_en'] or name} misses the shot", zh_lines, en_lines)
        else:
            _pair(f"{name}出手", f"{shot['name_en'] or name} takes a shot", zh_lines, en_lines)
        if not with_result:
            break


def _say_xfg(shots, zh_lines, en_lines) -> None:
    values = [shot["xfg"] for shot in shots if isinstance(shot["xfg"], (int, float))]
    if not values:
        _missing(zh_lines, en_lines)
        return
    for value in values:
        shown = _fmt(value)
        _pair(f"预期命中率 {shown}%", f"The expected make rate is {shown} percent", zh_lines, en_lines)


def _say_defenders(results, zh_lines, en_lines) -> None:
    names = _people_from_contacts(results)
    if not names:
        _missing(zh_lines, en_lines)
        return
    for name in names:
        _pair(f"{name}防守", f"{name} is the defender", zh_lines, en_lines)


def _say_season_three(results, zh_lines, en_lines) -> None:
    said = False
    for player in _players(results):
        zone = (player.get("offense") or {}).get("three_point") or {}
        pct = zone.get("fg_pct")
        if pct is None:
            continue
        label = player.get("name_zh") or player.get("player_name")
        _pair(
            f"{label}三分命中率 {_fmt(pct)}%",
            f"{player.get('name_en') or player.get('player_name')} three-point percentage is {_fmt(pct)}",
            zh_lines,
            en_lines,
        )
        said = True
    if not said:
        _missing(zh_lines, en_lines)


def _say_defense(results, zh_lines, en_lines) -> None:
    said = False
    for player in _players(results):
        defense = player.get("defense") or {}
        label = player.get("name_zh") or player.get("player_name")
        en_name = player.get("name_en") or player.get("player_name")
        for zone, zh_label, en_label in ZONE_ZH:
            pct = (defense.get(zone) or {}).get("opponent_fg_pct")
            if pct is None:
                continue
            prefix = "对手" if zone != "near_six_ft" else ""
            _pair(
                f"{label}{prefix}{zh_label} {_fmt(pct)}%",
                f"{en_name} defensive {en_label} is {_fmt(pct)}",
                zh_lines,
                en_lines,
            )
            said = True
    if not said:
        _missing(zh_lines, en_lines)


def _say_team(results, side: str, zh_lines, en_lines) -> None:
    teams = _teams(results)
    if not teams:
        _missing(zh_lines, en_lines)
        return
    side_zh = "进攻" if side == "offense" else "防守"
    side_en = "offensive" if side == "offense" else "defensive"
    for team in teams:
        label = TEAM_ZH.get(team.get("team") or "", team.get("team") or "")
        block = team.get(side) or {}
        for zone, zh_label, en_label in ZONE_ZH:
            pct = (block.get(zone) or {}).get("fg_pct")
            if pct is None:
                continue
            _pair(
                f"{label}{side_zh}{zh_label} {_fmt(pct)}%",
                f"{team.get('team')} {side_en} {en_label} is {_fmt(pct)}",
                zh_lines,
                en_lines,
            )


def _score_line(away, home, away_code: str, home_code: str) -> tuple[str, str]:
    away_zh = TEAM_ZH.get(away_code, "客场")
    home_zh = TEAM_ZH.get(home_code, "主场")
    return f"{away_zh} {away}，{home_zh} {home}", f"{away_code or 'away'} {away}, {home_code or 'home'} {home}"


def _sides(results: list[dict]) -> tuple[str, str]:
    finals = _finals(results)
    if not finals:
        return "", ""
    return str(finals[0].get("away") or ""), str(finals[0].get("home") or "")


def _say_score(shots, results, zh_lines, en_lines) -> None:
    away_code, home_code = _sides(results)
    pairs = []
    for shot in shots:
        away, home = shot.get("score_away"), shot.get("score_home")
        if away is None or home is None:
            continue
        pair = (away, home)
        if pair not in pairs:
            pairs.append(pair)
    if not pairs:
        _missing(zh_lines, en_lines)
        return
    for away, home in pairs:
        zh, en = _score_line(away, home, away_code, home_code)
        _pair(zh, en, zh_lines, en_lines)


def _say_final(results, zh_lines, en_lines) -> None:
    finals = _finals(results)
    if not finals:
        _missing(zh_lines, en_lines)
        return
    final = finals[0]
    zh, en = _score_line(final.get("away_score"), final.get("home_score"), final.get("away") or "", final.get("home") or "")
    _pair(zh, en, zh_lines, en_lines)


def _say_wingspan(question: str, results, zh_lines, en_lines) -> None:
    for player in _players(results):
        label = player.get("name_zh") or ""
        en_name = player.get("name_en") or player.get("player_name") or ""
        if label and label not in question and en_name not in question:
            continue
        profile = player.get("profile") or {}
        span = profile.get("wingspan_cm")
        missing = player.get("missing") or []
        if span is None or "wingspan_cm" in missing:
            _pair("数据里没有臂展", "The tools do not have a wingspan", zh_lines, en_lines)
            return
    _missing(zh_lines, en_lines)


def make_converse(question: str):
    def converse(messages, *, tool_choice):
        del tool_choice
        results = _payloads(messages)
        uses = _uses(messages)
        if not uses:
            return _tools([
                ("read_manifest", {}),
                ("read_identity", {}),
                ("read_official", {}),
            ])
        official_ready = any(name == "read_official" and (payload or {}).get("game_id") for name, payload in uses)
        season_ready = any(name == "read_season" for name, _payload in uses)
        calls = []
        if not official_ready:
            game_id = _game_id(results)
            if game_id:
                calls.append(("read_official", {"game_id": game_id}))
        if not season_ready:
            calls.extend(("read_season", query) for query in _season_queries(question))
        if calls:
            return _tools(calls)
        return {"role": "assistant", "content": [{"text": draft(question, results)}]}

    return converse


def simulate_one(question: str, job_id: str, media: dict) -> dict:
    called = []
    original = converse_loop.dispatch

    def wrapped(name, payload, ctx):
        called.append(name)
        return original(name, payload, ctx)

    converse_loop.dispatch = wrapped
    started = time.perf_counter()
    try:
        result = agent.invoke(
            {"bucket": "media", "jobId": job_id, "question": question},
            s3=FakeS3(media),
            converse=make_converse(question),
        )
    finally:
        converse_loop.dispatch = original
    elapsed = time.perf_counter() - started
    answer = result.get("answer") if isinstance(result, dict) else {}
    if not isinstance(answer, dict):
        answer = {}
    return {
        "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
        "elapsed_s": round(elapsed, 3),
        "tools": called,
        "answer": answer,
        "error": "",
    }


def live_one(url: str, job_id: str, question: str, timeout: float) -> dict:
    body = json.dumps({"jobId": job_id, "question": question}, ensure_ascii=False).encode()
    started = time.perf_counter()
    status = 0
    payload = {}
    error = ""
    for attempt in range(2):
        request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                payload = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"error": raw[:300]}
            if status in {502, 503, 504} and attempt == 0:
                continue
            error = str(payload.get("error") or status)
            break
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            if attempt == 0:
                continue
            break
    elapsed = time.perf_counter() - started
    answer = payload.get("answer") if isinstance(payload, dict) else {}
    if not isinstance(answer, dict):
        answer = {}
    sources = answer.get("sources") if isinstance(answer.get("sources"), list) else []
    return {
        "ok": status == 200 and bool(payload.get("ok", True)),
        "status": status,
        "elapsed_s": round(elapsed, 3),
        "tools": ["sources"] if sources else [],
        "answer": answer,
        "error": error,
    }


def _blob(answer: dict) -> str:
    return f"{answer.get('zh') or ''}\n{answer.get('en') or ''}"


def _has_number(text: str, target) -> bool:
    for token in NUMBER_RE.findall(text):
        try:
            value = float(token)
        except ValueError:
            continue
        if abs(value - float(target)) <= 0.05:
            return True
    return False


def _has_name(text: str, name: str) -> bool:
    return name.lower() in text.lower()


def score_one(spec: dict, row: dict) -> dict:
    answer = row.get("answer") or {}
    text = _blob(answer)
    zh = str(answer.get("zh") or "")
    checks = {}
    checks["tools"] = bool(row.get("tools"))
    if spec["lang"] == "zh":
        checks["zh"] = bool(CJK_RE.search(zh))
    else:
        checks["zh"] = True
    content = True
    for group in spec.get("need_names") or []:
        if not any(_has_name(text, name) for name in group):
            content = False
    for name in spec.get("forbid_names") or []:
        if _has_name(text, name):
            content = False
    for number in spec.get("need_numbers") or []:
        if not _has_number(text, number):
            content = False
    for group in spec.get("need_any") or []:
        if not any(token.lower() in text.lower() for token in group):
            content = False
    for token in spec.get("need_all") or []:
        if token.lower() not in text.lower():
            content = False
    if spec.get("forbid_any_number") and NUMBER_RE.search(text):
        content = False
    checks["content"] = content
    checks["pass"] = checks["tools"] and checks["zh"] and checks["content"]
    return checks


def run_mode(mode: str, pack: dict, media: dict, *, timeout: float, url: str) -> dict:
    job_id = pack["jobId"]
    rows = []
    for spec in pack["questions"]:
        if mode == "live":
            row = live_one(url, job_id, spec["question"], timeout)
        else:
            row = simulate_one(spec["question"], job_id, media)
        row["id"] = spec["id"]
        row["lang"] = spec["lang"]
        row["trap"] = spec["trap"]
        row["question"] = spec["question"]
        row["checks"] = score_one(spec, row)
        rows.append(row)
        mark = "过" if row["checks"]["pass"] else "不过"
        print(f"{mode} {spec['id']} {mark} {row['elapsed_s']}s", flush=True)
    return summarize(mode, rows)


def summarize(mode: str, rows: list[dict]) -> dict:
    def count(pred) -> int:
        return sum(1 for row in rows if pred(row))

    total = len(rows)
    zh_rows = [row for row in rows if row["lang"] == "zh"]
    return {
        "mode": mode,
        "total": total,
        "passed": count(lambda row: row["checks"]["pass"]),
        "content": count(lambda row: row["checks"]["content"]),
        "tools": count(lambda row: row["checks"]["tools"]),
        "zh": sum(1 for row in zh_rows if row["checks"]["zh"]),
        "zh_total": len(zh_rows),
        "traps": count(lambda row: row["trap"]),
        "traps_passed": count(lambda row: row["trap"] and row["checks"]["pass"]),
        "rows": rows,
    }


def _pct(passed: int, total: int) -> str:
    if not total:
        return "0/0"
    return f"{passed}/{total}"


def render_report(pack: dict, runs: list[dict], *, live_note: str, before: dict | None) -> str:
    lines = [
        "# DeepCourtAnalyst 评测",
        "",
        f"考试片比赛 `{pack['gameId']}`，jobId `{pack['jobId']}`（线上 `replay/clips.js` 里 `exam` 的成片是 `assets/exam-broadcast.mp4`）。",
        f"问答入口 `{pack.get('ask_url') or ASK_URL}`。",
        "参考数字只在 `questions.json`，不在提示词和工具里。",
        "",
    ]
    if live_note:
        lines.extend([live_note, ""])
    after = next((run for run in runs if run["mode"] == "simulate"), None)
    if before and after:
        lines.extend([
            "## 模拟模式修前修后",
            "",
            "| | 修前 | 修后 |",
            "| --- | --- | --- |",
            f"| 通过（用了工具、中文题有中文、内容和陷阱都对） | {_pct(before['passed'], before['total'])} | {_pct(after['passed'], after['total'])} |",
            f"| 内容对上 | {_pct(before['content'], before['total'])} | {_pct(after['content'], after['total'])} |",
            f"| 调用了工具 | {_pct(before['tools'], before['total'])} | {_pct(after['tools'], after['total'])} |",
            f"| 陷阱题通过 | {_pct(before['traps_passed'], before['traps'])} | {_pct(after['traps_passed'], after['traps'])} |",
            "",
        ])
    for run in runs:
        title = "模拟模式" if run["mode"] == "simulate" else "线上接口"
        lines.extend([
            f"## {title}",
            "",
            f"通过 {_pct(run['passed'], run['total'])}。内容对上 {_pct(run['content'], run['total'])}。用了工具 {_pct(run['tools'], run['total'])}。中文题有中文 {_pct(run['zh'], run['zh_total'])}。陷阱题通过 {_pct(run['traps_passed'], run['traps'])}。",
            "",
            "| 题 | 结果 | 工具 | 中文 | 内容 | 秒 | 回答 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ])
        for row in run["rows"]:
            checks = row["checks"]
            answer = row.get("answer") or {}
            shown = (answer.get("zh") or answer.get("en") or row.get("error") or "").replace("\n", " ")
            if len(shown) > 80:
                shown = shown[:80] + "…"
            lines.append(
                f"| {row['id']} | {'过' if checks['pass'] else '不过'} | {'是' if checks['tools'] else '否'} | "
                f"{'是' if checks['zh'] else '否'} | {'是' if checks['content'] else '否'} | {row['elapsed_s']} | {shown} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="评测 DeepCourtAnalyst")
    parser.add_argument("--mode", choices=["simulate", "live", "both"], default="both")
    parser.add_argument("--timeout", type=float, default=40)
    parser.add_argument("--url", default=ASK_URL)
    parser.add_argument("--out", default="")
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--before", default="")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    pack = load_questions()
    media = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    modes = ["simulate", "live"] if args.mode == "both" else [args.mode]
    runs = []
    live_note = ""
    for mode in modes:
        if mode == "live":
            try:
                probe = urllib.request.urlopen(args.url.replace("/agent/ask", "/agent/analysis/exam"), timeout=15)
                probe.read(200)
            except Exception as exc:  # noqa: BLE001
                live_note = f"线上接口这次没有跑起来：{exc}。下面只有模拟模式。"
                print(live_note, flush=True)
                continue
        runs.append(run_mode(mode, pack, media, timeout=args.timeout, url=args.url))
    before = None
    if args.before:
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
        if before.get("runs"):
            before = next(item for item in before["runs"] if item["mode"] == "simulate")
        if before and before.get("rows"):
            before = summarize(before.get("mode") or "simulate", before["rows"])
    payload = {"jobId": pack["jobId"], "gameId": pack["gameId"], "live_note": live_note, "runs": runs}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_report:
        text = render_report(pack, runs, live_note=live_note, before=before)
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"wrote {args.report}", flush=True)


if __name__ == "__main__":
    main()
