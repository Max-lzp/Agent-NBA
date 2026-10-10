#!/usr/bin/env python3
"""用 aws bedrock-runtime converse 看一帧。

一律 --profile nba --region us-east-1。图像字节走 CLI 的 base64 blob，
不把 base64 写进仓库。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

VISION = Path(__file__).resolve().parent
WORK = VISION / "work"
MODEL_ID = "qwen.qwen3-vl-235b-a22b"
KIMI_MODEL_ID = "us.moonshotai.kimi-k3"
PROFILE = "nba"
REGION = "us-east-1"
ENV_LOCAL = VISION / ".env.local"
# Bedrock On-Demand, US East, Qwen3 VL 235B A22B（2026-10-07 价目页）
PRICE_INPUT_PER_TOKEN = 0.53 / 1_000_000
PRICE_OUTPUT_PER_TOKEN = 2.66 / 1_000_000
# Kimi K3 US 跨区域推理，Standard，每百万 token（模型卡 2026-09）
KIMI_INPUT_PER_TOKEN = 3.30 / 1_000_000
KIMI_OUTPUT_PER_TOKEN = 16.50 / 1_000_000


# 实测 PNG 2.72MB 可通过，2.92MB 报 request body length limit exceeded。
SAFE_IMAGE_BYTES = 2_500_000


def prepare_image(path: Path) -> tuple[Path, dict]:
    meta = {
        "source": str(path),
        "source_bytes": path.stat().st_size,
        "transcoded": False,
    }
    if path.stat().st_size <= SAFE_IMAGE_BYTES:
        meta["sent_bytes"] = meta["source_bytes"]
        return path, meta
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(f"帧 {path.name} 超过 {SAFE_IMAGE_BYTES} 字节，且没有 ffmpeg 可转 JPEG")
    dest_dir = WORK / "jpeg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.stem}.jpg"
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-q:v", "3", str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not dest.is_file():
        raise SystemExit(proc.stderr.strip() or "JPEG 转码失败")
    meta.update({"transcoded": True, "sent_bytes": dest.stat().st_size, "sent_format": "jpeg"})
    return dest, meta


def image_block(path: Path) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".")
    fmt = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(suffix)
    if fmt is None:
        raise SystemExit(f"不支持的图像格式: {path.suffix}")
    return {"image": {"format": fmt, "source": {"bytes": encoded}}}


def build_payload(
    image: Path,
    user_text: str,
    system_text: str | None,
    max_tokens: int,
    model_id: str | None = None,
) -> dict:
    payload: dict = {
        "modelId": model_id or MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    image_block(image),
                    {"text": user_text},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }
    if system_text:
        payload["system"] = [{"text": system_text}]
    return payload


def bedrock_api_key() -> str:
    """Bedrock 长期 API Key。环境变量优先，否则读 gitignore 的 .env.local。"""
    key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip()
    if not key and ENV_LOCAL.is_file():
        for line in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "AWS_BEARER_TOKEN_BEDROCK":
                key = value.strip().strip('"').strip("'")
                break
    if not key:
        raise SystemExit(
            "缺少 API Key。写到 pipeline/vision/.env.local："
            "AWS_BEARER_TOKEN_BEDROCK=你的Key。不要贴到对话或提交到仓库。"
        )
    return key


def estimate_cost(usage: dict, model_id: str | None = None) -> dict:
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    prices = {
        "qwen.qwen3-vl-235b-a22b": (PRICE_INPUT_PER_TOKEN, PRICE_OUTPUT_PER_TOKEN),
        KIMI_MODEL_ID: (KIMI_INPUT_PER_TOKEN, KIMI_OUTPUT_PER_TOKEN),
        "global.moonshotai.kimi-k3": (3.00 / 1_000_000, 15.00 / 1_000_000),
        "kimi-k3": (3.00 / 1_000_000, 15.00 / 1_000_000),
    }
    input_rate, output_rate = prices.get(model_id or MODEL_ID, (None, None))
    if input_rate is None:
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(usage.get("totalTokens") or (input_tokens + output_tokens)),
            "input_usd": None,
            "output_usd": None,
            "total_usd": None,
        }
    input_usd = input_tokens * input_rate
    output_usd = output_tokens * output_rate
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("totalTokens") or (input_tokens + output_tokens)),
        "input_usd": round(input_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": round(input_usd + output_usd, 6),
        "price_per_1m_input_usd": input_rate * 1_000_000,
        "price_per_1m_output_usd": output_rate * 1_000_000,
    }


def _message_text(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    reasoning = message.get("reasoning_content") or ""
    if "{" in content:
        return content
    if "{" in reasoning:
        return reasoning
    return content or reasoning


def converse_with_api_key(
    image: Path,
    user_text: str,
    system_text: str | None,
    max_tokens: int,
    model_id: str | None = None,
) -> dict:
    """Kimi K3 走 Chat Completions。sk- Key 打 Moonshot，其余打 Bedrock。"""
    key = bedrock_api_key()
    moonshot = key.startswith("sk-")
    if key.startswith("sk-kimi-"):
        model_id = "kimi-k3"
        url = "https://api.kimi.com/coding/v1/chat/completions"
        profile = "kimi-code"
        region = "api.kimi.com"
    elif moonshot:
        model_id = "kimi-k3"
        url = "https://api.moonshot.ai/v1/chat/completions"
        profile = "moonshot"
        region = "api.moonshot.ai"
    else:
        model_id = model_id or KIMI_MODEL_ID
        url = f"https://bedrock-runtime.{REGION}.amazonaws.com/openai/v1/chat/completions"
        profile = "bedrock-api-key"
        region = REGION
    sent, image_meta = prepare_image(image)
    encoded = base64.b64encode(sent.read_bytes()).decode("ascii")
    suffix = sent.suffix.lower().lstrip(".")
    fmt = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(suffix)
    if fmt is None:
        raise SystemExit(f"不支持的图像格式: {sent.suffix}")
    mime = "image/jpeg" if fmt == "jpeg" else f"image/{fmt}"
    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"},
                },
                {"type": "text", "text": user_text},
            ],
        }
    )
    # kimi-k3 的 temperature 固定为 1，传 0 会被拒。推理档用 low，避免每帧长思考。
    body = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max(max_tokens, 4096),
        "reasoning_effort": "low",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"Kimi K3 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Kimi K3 连不上: {exc.reason}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    message = ((payload.get("choices") or [{}])[0].get("message") or {})
    raw_usage = payload.get("usage") or {}
    usage = {
        "inputTokens": int(raw_usage.get("prompt_tokens") or 0),
        "outputTokens": int(raw_usage.get("completion_tokens") or 0),
        "totalTokens": int(raw_usage.get("total_tokens") or 0),
    }
    return {
        "model_id": model_id,
        "profile": profile,
        "region": region,
        "image": str(image),
        "image_bytes": image.stat().st_size,
        "image_meta": image_meta,
        "elapsed_ms": elapsed_ms,
        "latency_ms": None,
        "stop_reason": (payload.get("choices") or [{}])[0].get("finish_reason"),
        "text": _message_text(message).strip(),
        "usage": usage,
        "cost": estimate_cost(usage, model_id),
    }


def converse(
    image: Path,
    user_text: str,
    system_text: str | None,
    max_tokens: int,
    model_id: str | None = None,
) -> dict:
    model_id = model_id or MODEL_ID
    if "kimi-k3" in model_id:
        return converse_with_api_key(image, user_text, system_text, max_tokens, model_id)
    WORK.mkdir(parents=True, exist_ok=True)
    sent, image_meta = prepare_image(image)
    payload_path = WORK / f"converse_payload_{os.getpid()}_{time.time_ns()}.json"
    payload_path.write_text(
        json.dumps(build_payload(sent, user_text, system_text, max_tokens, model_id)),
        encoding="utf-8",
    )
    cmd = [
        "aws",
        "bedrock-runtime",
        "converse",
        "--cli-input-json",
        f"file://{payload_path}",
    ]
    # profile nba 走 nba-sso。那个会话过期、但 team04 仍可用时，
    # 调用方先假定 ParticipantRole 并写入环境变量，这里就不再带 --profile。
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        cmd.extend(["--profile", PROFILE])
    cmd.extend(["--region", REGION, "--output", "json"])
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    finally:
        payload_path.unlink(missing_ok=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"aws 退出码 {proc.returncode}")
    response = json.loads(proc.stdout)
    texts = []
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "text" in block:
            texts.append(block["text"])
    usage = response.get("usage") or {}
    return {
        "model_id": model_id,
        "profile": PROFILE,
        "region": REGION,
        "image": str(image),
        "image_bytes": image.stat().st_size,
        "image_meta": image_meta,
        "elapsed_ms": elapsed_ms,
        "latency_ms": (response.get("metrics") or {}).get("latencyMs"),
        "stop_reason": response.get("stopReason"),
        "text": "\n".join(texts).strip(),
        "usage": usage,
        "cost": estimate_cost(usage, model_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="单帧调用 Qwen3 VL")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--user-text", required=True)
    parser.add_argument("--system-file", type=Path)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    system_text = args.system_file.read_text(encoding="utf-8") if args.system_file else None
    result = converse(args.image, args.user_text, system_text, args.max_tokens)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cost = result["cost"]
    print(
        f"{args.image.name}: in={cost['input_tokens']} out={cost['output_tokens']} "
        f"${cost['total_usd']:.4f} {result['elapsed_ms']}ms"
    )
    print(result["text"][:1200])


if __name__ == "__main__":
    main()
