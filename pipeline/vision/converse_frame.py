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
from pathlib import Path

VISION = Path(__file__).resolve().parent
WORK = VISION / "work"
MODEL_ID = "qwen.qwen3-vl-235b-a22b"
PROFILE = "nba"
REGION = "us-east-1"
# Bedrock On-Demand, US East, Qwen3 VL 235B A22B（2026-10-07 价目页）
PRICE_INPUT_PER_TOKEN = 0.53 / 1_000_000
PRICE_OUTPUT_PER_TOKEN = 2.66 / 1_000_000


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


def build_payload(image: Path, user_text: str, system_text: str | None, max_tokens: int) -> dict:
    payload: dict = {
        "modelId": MODEL_ID,
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


def estimate_cost(usage: dict) -> dict:
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    input_usd = input_tokens * PRICE_INPUT_PER_TOKEN
    output_usd = output_tokens * PRICE_OUTPUT_PER_TOKEN
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("totalTokens") or (input_tokens + output_tokens)),
        "input_usd": round(input_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": round(input_usd + output_usd, 6),
        "price_per_1m_input_usd": 0.53,
        "price_per_1m_output_usd": 2.66,
    }


def converse(image: Path, user_text: str, system_text: str | None, max_tokens: int) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    sent, image_meta = prepare_image(image)
    payload_path = WORK / f"converse_payload_{os.getpid()}_{time.time_ns()}.json"
    payload_path.write_text(
        json.dumps(build_payload(sent, user_text, system_text, max_tokens)),
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
        "model_id": MODEL_ID,
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
        "cost": estimate_cost(usage),
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
