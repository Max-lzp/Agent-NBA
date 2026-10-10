#!/usr/bin/env python3
"""把本机新出的片名并进已有回放数据，并设成 CloudFront 根路径的默认片。

本地 present 写的 clips.js 没有 DEEPCOURT_LEAD。页面在没有 LEAD 时会打开 flagg。
这里保留网页桶里已有的其他片名，只换这一条，并写上 LEAD。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def parse_clips_js(text: str) -> dict:
    marker = "window.DEEPCOURT_CLIPS="
    start = text.find(marker)
    if start < 0:
        raise ValueError("回放数据不是预期格式")
    raw = text[start + len(marker):].strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("回放数据不是对象")
    return data


def merge_text(clip: str, local: dict, remote: dict | None) -> str:
    if not TOKEN.fullmatch(clip):
        raise ValueError("片名只能用字母、数字、下划线和中划线")
    if clip not in local:
        raise ValueError(f"本地回放数据里没有 {clip}")
    merged = dict(remote or {})
    merged[clip] = local[clip]
    body = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    return f'window.DEEPCOURT_LEAD="{clip}";\nwindow.DEEPCOURT_CLIPS={body};\n'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        print("用法：merge_lead.py <片名> <本地 clips.js> <远端 clips.js> <输出>", file=sys.stderr)
        return 2
    clip, local_path, remote_path, dest = args
    local = parse_clips_js(Path(local_path).read_text(encoding="utf-8"))
    remote_file = Path(remote_path)
    remote = {}
    if remote_file.is_file() and remote_file.stat().st_size:
        remote = parse_clips_js(remote_file.read_text(encoding="utf-8"))
    Path(dest).write_text(merge_text(clip, local, remote), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
