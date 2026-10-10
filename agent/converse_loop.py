"""Bedrock Converse 工具循环。模型决定查什么，落地校验决定哪句话能留下。"""

from __future__ import annotations

import json
import os
import re

from grounding import FILLER, NO_ANSWER, fill_pair, sources_for
from manifest import check
from tools import TOOL_SPECS, Ctx, dispatch

MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME = "bedrock-agentcore"

SYSTEM = """你是 Deep Court 分析师。你只能根据工具返回的内容说话。

规则：
- 先调用工具，再写结论。事件清单、官方逐回合、赛季攻防三区、认人结果，没查到的不要用。
- 数字和人名必须原样来自工具。不要估算，不要把 null 当成 0，出手数为 0 时不要说命中率是 0%。
- 工具里的百分数已经是 0 到 100。命中率单独写，null 就说没有这项数据。
- 问题问到的字段如果工具结果里没有，或者在 missing 里，就明确说「数据里没有」。不要拿别的数字顶上，也不要把空值说成 0。
- 同一段回放里有几次出手就说几次：谁投的、进没进、预期命中率用 xfg_pct。盖帽和犯规只点工具里写出的人。比分用 score_away、score_home 或 final，并带上 HOU/DAL。
- 不要把一名球员的赛季数据安到另一名球员身上。中文名只用工具里的 alias_zh 或 name_zh。纳吉·马绍尔写作马绍尔，不要写成马歇尔。
- 问到火箭或独行侠整队的攻防三区时，用 read_season 查队名。那是队内球员计数相加，不要另编一组百分比。
- 每个回合、每次出手写一两句口语解说。定格卡只写一句话。亮点最多三条，必须是不同的句子。没有更多数据就少写，不要用同一句凑满三条。
- 中文和英文都要写。观众用中文提问时，answer.zh 不能空。
- 每句话的 sources 用工具结果里的 source 字段。
- 最后只输出一个 JSON 对象，不要 Markdown。

管道模式：
{"possessions":[{"possession_id":"","commentary_zh":"","commentary_en":"","sources":[]}],"shots":[{"t":0,"freeze_zh":"","freeze_en":"","commentary_zh":"","commentary_en":"","sources":[]}],"highlights":[{"zh":"","en":"","sources":[]},{"zh":"","en":"","sources":[]},{"zh":"","en":"","sources":[]}]}

问答模式在同一对象里再加 "answer":{"zh":"","en":"","sources":[]}。只回答观众的问题。
"""


def extract_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _text_of(message: dict) -> str:
    chunks = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("text"):
            chunks.append(block["text"])
    return "\n".join(chunks)


def _tool_uses(message: dict) -> list[dict]:
    uses = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("toolUse"), dict):
            uses.append(block["toolUse"])
    return uses


def _field(item: dict, *names: str) -> str:
    for name in names:
        value = item.get(name)
        if isinstance(value, str):
            return value
    return ""


def _line(item: dict, evidence: list, zh_keys, en_keys, *, question: str = "", require_zh: bool = False) -> tuple[str, str, list]:
    zh, en = fill_pair(
        _field(item, *zh_keys),
        _field(item, *en_keys),
        evidence,
        question=question,
        require_zh=require_zh,
    )
    sources = sources_for(f"{zh} {en}".strip(), item.get("sources"), evidence)
    return zh, en, sources


def _highlight_key(text: str) -> str:
    folded = re.sub(r"\s+", "", text or "").lower()
    return folded.strip("。！？!?，、；;：:")


def _dedupe_highlights(highlights: list[dict]) -> list[dict]:
    """同一句中文或同一句英文只留一条。没有中文时才按英文去重。"""
    kept = []
    seen = set()
    for item in highlights:
        zh = _highlight_key(item.get("zh") or "")
        en = _highlight_key(item.get("en") or "")
        if zh:
            key = ("zh", zh)
        elif en:
            key = ("en", en)
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def normalize(raw: dict, evidence: list, *, mode: str, job_id: str, question: str = "") -> dict:
    possessions = []
    for item in raw.get("possessions") or []:
        if not isinstance(item, dict):
            continue
        zh, en, sources = _line(item, evidence, ("commentary_zh", "commentaryZh"), ("commentary_en", "commentaryEn"))
        if not zh and not en:
            continue
        possessions.append({
            "possessionId": str(item.get("possession_id") or item.get("possessionId") or ""),
            "commentaryZh": zh,
            "commentaryEn": en,
            "sources": sources,
        })
    shots = []
    for item in raw.get("shots") or []:
        if not isinstance(item, dict):
            continue
        freeze_zh, freeze_en, freeze_sources = _line(item, evidence, ("freeze_zh", "freezeZh"), ("freeze_en", "freezeEn"))
        zh, en, sources = _line(item, evidence, ("commentary_zh", "commentaryZh"), ("commentary_en", "commentaryEn"))
        if not any((freeze_zh, freeze_en, zh, en)):
            continue
        try:
            stamp = float(item.get("t")) if item.get("t") is not None else None
        except (TypeError, ValueError):
            stamp = None
        shots.append({
            "t": stamp,
            "freezeZh": freeze_zh,
            "freezeEn": freeze_en,
            "commentaryZh": zh,
            "commentaryEn": en,
            "sources": list(dict.fromkeys(freeze_sources + sources)),
        })
    highlights = []
    for item in raw.get("highlights") or []:
        if not isinstance(item, dict):
            continue
        zh, en, sources = _line(item, evidence, ("zh", "commentary_zh"), ("en", "commentary_en"))
        if not zh and not en:
            continue
        highlights.append({"zh": zh, "en": en, "sources": sources})
    highlights = _dedupe_highlights(highlights)[:3]
    if mode == "pipeline" and not highlights:
        highlights.append({"zh": FILLER["zh"], "en": FILLER["en"], "sources": []})
    analysis = {
        "version": "1",
        "jobId": job_id,
        "mode": mode,
        "model": MODEL_ID,
        "possessions": possessions,
        "shots": shots,
        "highlights": highlights,
    }
    if mode == "qa":
        answer = raw.get("answer") if isinstance(raw.get("answer"), dict) else {}
        if isinstance(raw.get("answer"), str):
            answer = {"zh": raw["answer"]}
        needs_zh = bool(re.search(r"[\u4e00-\u9fff]", question or ""))
        zh, en, sources = _line(
            answer, evidence, ("zh", "commentary_zh"), ("en", "commentary_en"),
            question=question, require_zh=needs_zh,
        )
        if not zh and not en:
            zh, en, sources = NO_ANSWER["zh"], NO_ANSWER["en"], []
        elif needs_zh and not zh:
            zh = NO_ANSWER["zh"]
        analysis["answer"] = {"zh": zh, "en": en, "sources": sources}
    return analysis


def empty_analysis(job_id: str, mode: str) -> dict:
    return normalize({}, [], mode=mode, job_id=job_id)


def user_text(payload: dict, mode: str) -> str:
    job_id = payload.get("jobId") or ""
    if mode == "qa":
        return f"jobId={job_id}。观众问：{payload.get('question')}。只根据工具数据回答。"
    return f"jobId={job_id}。key={payload.get('key') or ''}。请先校验并阅读清单，再写这段回放的解说。"


def run_loop(payload: dict, ctx: Ctx, converse, mode: str) -> dict:
    messages = [{"role": "user", "content": [{"text": user_text(payload, mode)}]}]
    limit = 4 if mode == "qa" else 6
    draft: dict = {}
    for turn in range(limit):
        message = converse(messages, tool_choice={"any": {}} if turn == 0 else {"auto": {}})
        messages.append(message)
        uses = _tool_uses(message)
        if uses:
            results = []
            for use in uses:
                result = dispatch(str(use.get("name") or ""), use.get("input") or {}, ctx)
                results.append({
                    "toolResult": {
                        "toolUseId": use.get("toolUseId") or "tool",
                        "content": [{"text": json.dumps(result, ensure_ascii=False)}],
                    }
                })
            messages.append({"role": "user", "content": results})
            continue
        draft = extract_json(_text_of(message))
        break
    return normalize(
        draft,
        ctx.evidence,
        mode=mode,
        job_id=ctx.job_id,
        question=str(payload.get("question") or ""),
    )


def bedrock_converse(messages, *, tool_choice):
    import boto3
    from botocore.config import Config

    client = getattr(bedrock_converse, "_client", None)
    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            config=Config(connect_timeout=3, read_timeout=60, retries={"max_attempts": 2}),
        )
        bedrock_converse._client = client
    response = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=messages,
        toolConfig={"tools": TOOL_SPECS, "toolChoice": tool_choice},
        inferenceConfig={"maxTokens": 1800, "temperature": 0},
    )
    return response["output"]["message"]


def pipeline(payload: dict, ctx: Ctx, converse=None) -> dict:
    if ctx.manifest is None:
        ctx.manifest = ctx.media.get_json(ctx.key)
    problems = check(ctx.manifest)
    if problems:
        return {
            "ok": False,
            "reason": "invalid_manifest",
            "error": "；".join(problems),
            "stream": None,
            "runtime": RUNTIME,
            "mode": "pipeline",
        }
    caller = converse or bedrock_converse
    try:
        analysis = run_loop(payload, ctx, caller, "pipeline")
    except Exception as exc:  # noqa: BLE001 — 模型失败仍交还清单，让页面事件先落下去
        analysis = empty_analysis(ctx.job_id, "pipeline")
        analysis["note"] = "模型这次没有返回可用解说。"
        analysis["error"] = str(exc)[:300]
    try:
        ctx.media.put_json(f"analysis/{ctx.job_id}.json", analysis)
    except Exception as exc:  # noqa: BLE001 — 解说写失败仍交还清单，页面事件不能停住
        analysis["note"] = "解说没有写进媒体桶。"
        analysis["error"] = str(exc)[:300]
    return {
        "ok": True,
        "error": "",
        "stream": ctx.manifest,
        "runtime": RUNTIME,
        "mode": "pipeline",
        "analysis": analysis,
    }


def answer(payload: dict, ctx: Ctx, converse=None) -> dict:
    caller = converse or bedrock_converse
    try:
        analysis = run_loop(payload, ctx, caller, "qa")
    except Exception as exc:  # noqa: BLE001
        analysis = empty_analysis(ctx.job_id, "qa")
        analysis["answer"] = dict(NO_ANSWER)
        analysis["error"] = str(exc)[:300]
    return {
        "ok": True,
        "error": "",
        "runtime": RUNTIME,
        "mode": "qa",
        "jobId": ctx.job_id,
        "answer": analysis.get("answer") or dict(NO_ANSWER),
    }
