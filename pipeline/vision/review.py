#!/usr/bin/env python3
"""本地回放：原视频叠检测框和传球/抢断。

用法（在 team-repo 下）:
  python3 pipeline/vision/review.py

浏览器打开 http://127.0.0.1:8765
视频支持拖动（HTTP Range）。叠加只在检测采样帧上更新，两帧之间保持上一帧的框。
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

VISION = Path(__file__).resolve().parent
ROOT = VISION.parents[1]
PAGE = VISION / "review.html"
CLIPS = json.loads((VISION / "clips.json").read_text(encoding="utf-8"))
LABELS = {"flagg": "弗拉格 2月1日", "amen": "阿门 1月4日"}


def video_path(clip_id: str) -> Path:
    return (ROOT / CLIPS[clip_id]["video"]).resolve()


def result_path(clip_id: str) -> Path:
    return VISION / "results" / "local" / f"{clip_id}.json"


class ReviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        if str(args[1:2]) == "('404',)":
            super().log_message(fmt, *args)

    def do_HEAD(self) -> None:
        self._route(head_only=True)

    def do_GET(self) -> None:
        self._route(head_only=False)

    def _route(self, head_only: bool) -> None:
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html", "/review.html"):
            self._send_bytes(PAGE.read_bytes(), "text/html; charset=utf-8", head_only)
            return
        if path in ("/api/clips", "/data/catalog.json"):
            body = json.dumps(self._catalog(), ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", head_only)
            return
        if path.startswith("/api/result/") or (path.startswith("/data/") and path.endswith(".json")):
            clip_id = path.removeprefix("/api/result/").removeprefix("/data/").removesuffix(".json")
            target = result_path(clip_id)
            if clip_id not in CLIPS or not target.is_file():
                self.send_error(404)
                return
            self._send_bytes(target.read_bytes(), "application/json; charset=utf-8", head_only)
            return
        if path.startswith("/media/"):
            clip_id = path.removeprefix("/media/").removesuffix(".mp4")
            if clip_id not in CLIPS:
                self.send_error(404)
                return
            target = video_path(clip_id)
            if not target.is_file():
                self.send_error(404, f"找不到视频 {target}")
                return
            self._send_range(target, "video/mp4", head_only)
            return
        self.send_error(404)

    def _catalog(self) -> list[dict]:
        rows = []
        for clip_id, clip in CLIPS.items():
            result = result_path(clip_id)
            summary = {}
            if result.is_file():
                doc = json.loads(result.read_text(encoding="utf-8"))
                summary = {
                    "frame_count": doc.get("frame_count"),
                    "duration_sec": doc.get("duration_sec"),
                    "sampled_fps": doc.get("sampled_fps"),
                    "events": len(doc.get("events") or []),
                }
            rows.append(
                {
                    "id": clip_id,
                    "label": LABELS.get(clip_id, clip_id),
                    "video_ready": video_path(clip_id).is_file(),
                    "result_ready": result.is_file(),
                    **summary,
                }
            )
        return rows

    def _send_bytes(self, body: bytes, content_type: str, head_only: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_range(self, path: Path, content_type: str, head_only: bool) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
            left, _, right = spec.partition("-")
            if left == "":
                length = int(right or "0")
                start = max(0, size - length)
            else:
                start = int(left)
                end = int(right) if right else size - 1
            start = max(0, start)
            end = min(size - 1, end)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if head_only:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="播放训练视频并叠上检测事件")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = None
    port = args.port
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer((args.host, port), ReviewHandler)
            break
        except OSError as exc:
            if exc.errno != 48:
                raise
    if server is None:
        raise SystemExit(f"{args.port} 起的 20 个端口都被占用")
    print(f"回放台 http://{args.host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    main()
