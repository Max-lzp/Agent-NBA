#!/usr/bin/env python3
"""录一支 Deep Court 演示视频。

打开 --base-url 上的播放器（?clip=），按镜头播放、跳时间线、等进球定格、
切换中英音轨，再打开 /readme 滚过管道和服务表。然后烧中文字幕。

没有 --polly 时保留页面原声。有 --polly 时用 Amazon Polly Neural 知语按句合成，
叠在压低的页面原声上。解说词和镜头时长见本文件的 SHOTS，正文在 narration.md。
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

WIDTH = 1920
HEIGHT = 1080
RATE = 48000
MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BASE = "https://d2vzd2batw1eqe.cloudfront.net"

# 计划总长 120 秒。偏移是镜头内的秒。
SHOTS = [
    {
        "id": "open",
        "title": "打开播放器",
        "seconds": 12,
        "cues": [
            (0, 6, "Team 04 · Deep Court\n打开就是成片，不用选文件，也不用等。"),
            (6, 12, "这支是训练片弗拉格，页面在 CloudFront。\n追踪、记分牌和认人，这支仍在本机。"),
        ],
    },
    {
        "id": "play",
        "title": "播放和数据叠加",
        "seconds": 18,
        "cues": [
            (0, 9, "节次、比赛钟和比分来自记分牌。\n人名来自球衣号码，对不上的坑先空着。"),
            (9, 18, "事件只来自结构化清单。\n模型不看图编发生了什么。"),
        ],
    },
    {
        "id": "timeline",
        "title": "时间线",
        "seconds": 8,
        "cues": [
            (0, 8, "沿时间线看接球、传球和出手。\n官方逐回合补投篮人、助攻和预期命中率。"),
        ],
    },
    {
        "id": "freeze",
        "title": "进球定格",
        "seconds": 15,
        "cues": [
            (0, 8, "进球后跳回出手帧，定格五秒。\n红圈是投篮人，黄色虚线圈是领防人。"),
            (8, 15, "卡片写队内排名。领防、脱手帧和队内排名，\n这支训练片是在本机算的。"),
        ],
    },
    {
        "id": "lang",
        "title": "中英音轨",
        "seconds": 15,
        "cues": [
            (0, 5, "切到英文原声，就是成片里的原解说。"),
            (5, 11, "切回中文：Transcribe 转写，Translate 翻译，\n再用 Polly Neural 知语按句合成。"),
            (11, 15, "训练片这两条音轨在本机做好，\n已经放进网页桶。考试片在云上做。"),
        ],
    },
    {
        "id": "floor",
        "title": "场上十人",
        "seconds": 8,
        "cues": [
            (0, 8, "这一回合场上十个人列在下面。\n引力和关键度是赛季汇总，不是这一球的胜率变化。"),
        ],
    },
    {
        "id": "readme",
        "title": "作品说明",
        "seconds": 16,
        "cues": [
            (0, 5, "同一域名的 /readme 说明作品。\n评委不用翻仓库。"),
            (5, 10, "事件只来自清单。官方逐回合补投篮人、\n助攻和预期命中率。AgentCore 只做校验。"),
            (10, 16, "输入经 API Gateway 进 S3 和 DynamoDB。\n输出是 CloudFront 上的成片和音轨。"),
        ],
    },
    {
        "id": "aws",
        "title": "AWS 服务",
        "seconds": 28,
        "cues": [
            (0, 5, "收片、校验、回放、网页是主路径。\n考试片追踪走 Batch，c7i.48xlarge，192 个 vCPU 保持开着。"),
            (5, 10, "记分牌在考试片那台 c7i 上，用 RapidOCR。\n这支训练片的记分牌仍在本机。"),
            (10, 17, "认人已接通。Qwen3 VL 看多帧，投票定号码和球衣颜色。\nRekognition 算第二票。只认场上十个人，一个名字一条轨迹。"),
            (17, 24, "官方逐回合先钉得分人和助攻人。Qwen 调失败才用 Textract。\n认不出名字的，即将挂队色标签，写着 Agent detecting。"),
            (24, 28, "成片用 ffmpeg。MediaConvert 还没交作业。\n密钥仍是空占位。"),
        ],
    },
]

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]

FONT_PREFERENCE = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Songti SC",
    "Heiti SC",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Droid Sans Fallback",
]


def log(message: str) -> None:
    print(message, flush=True)


def planned_total() -> float:
    return sum(shot["seconds"] for shot in SHOTS)


def srt_stamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def realize_cues(shots_done: list[dict]) -> list[tuple[float, float, str]]:
    """把镜头内的计划字幕铺到实际起止时间上。最后一句延伸到镜头结束。"""
    entries = []
    for shot in shots_done:
        cues = shot["cues"]
        start = shot["start"]
        end = shot["end"]
        planned = shot["seconds"] or 1
        actual = max(0.01, end - start)
        scale = actual / planned
        for index, (cue_start, cue_end, text) in enumerate(cues):
            left = start + cue_start * scale
            right = start + cue_end * scale
            if index == len(cues) - 1:
                right = end
            if right > left + 0.15:
                entries.append((left, right, text))
    return entries


def render_srt(entries: list[tuple[float, float, str]]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(entries, 1):
        blocks.append(f"{index}\n{srt_stamp(start)} --> {srt_stamp(end)}\n{text}\n")
    return "\n".join(blocks) + "\n"


def segments_from_cues(cues: list[dict]) -> list[dict]:
    """把 play / pause / seeked / ended 收成 {wall, dur, media, src}。

    seeked 在播放中会把上一段按墙钟截断，新媒体时间从 seek 之后算。
    暂停时的 seeked 只记住位置，不产生声音。
    """
    playing = False
    play_wall = 0.0
    play_media = 0.0
    src = ""
    segments = []

    def close(wall: float) -> None:
        nonlocal playing
        if not playing:
            return
        dur = wall - play_wall
        if dur > 0.08 and src:
            segments.append({
                "wall": max(0.0, play_wall),
                "dur": dur,
                "media": max(0.0, play_media),
                "src": src.split("#", 1)[0],
            })
        playing = False

    for cue in cues:
        kind = cue.get("type")
        wall = float(cue.get("wall") or 0)
        media = float(cue.get("media") or 0)
        cue_src = str(cue.get("src") or "")
        if kind == "play":
            close(wall)
            playing = True
            play_wall = wall
            play_media = media
            src = cue_src or src
        elif kind == "seeked":
            if playing:
                close(wall)
                playing = True
                play_wall = wall
                play_media = media
                if cue_src:
                    src = cue_src
            else:
                play_media = media
                if cue_src:
                    src = cue_src
        elif kind in ("pause", "ended"):
            close(wall)
    return segments


def font_families() -> set[str]:
    try:
        raw = subprocess.check_output(["fc-list", ":family"], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return set()
    names = set()
    for line in raw.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        for name in parts[1].split(","):
            cleaned = name.strip()
            if cleaned:
                names.add(cleaned)
    return names


def choose_font() -> str:
    families = font_families()
    for name in FONT_PREFERENCE:
        if name in families:
            return name
    if platform.system() == "Darwin":
        return "PingFang SC"
    return "WenQuanYi Micro Hei"


def escape_filter_path(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"ffmpeg 失败：{' '.join(args[:6])}\n{tail}")


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def extract_wav(src: Path, dest: Path) -> None:
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "2", "-ar", str(RATE),
        "-c:a", "pcm_s16le", str(dest),
    ])


def load_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise RuntimeError(f"{path} 不是 16-bit PCM")
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if rate != RATE or channels != 2:
        raise RuntimeError(f"{path} 需要 {RATE}Hz 立体声，实际 {rate}Hz {channels}ch")
    return samples


def write_wav(path: Path, samples: array.array) -> None:
    payload = array.array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(payload.tobytes())


def fade_edges(samples: array.array, fade_frames: int = 240) -> None:
    frames = len(samples) // 2
    fade = min(fade_frames, frames // 2)
    if fade <= 0:
        return
    for index in range(fade):
        gain = index / fade
        for channel in (0, 1):
            left = index * 2 + channel
            right = (frames - 1 - index) * 2 + channel
            samples[left] = int(samples[left] * gain)
            samples[right] = int(samples[right] * gain)


def mix_at(dst: array.array, src: array.array, dst_frame: int, src_frame: int, frames: int, gain: float) -> None:
    if frames <= 0 or gain == 0:
        return
    src_frame = max(0, src_frame)
    available = len(src) // 2 - src_frame
    frames = min(frames, available, len(dst) // 2 - dst_frame)
    if frames <= 0:
        return
    chunk = array.array("h", src[src_frame * 2:(src_frame + frames) * 2])
    fade_edges(chunk)
    dest_at = dst_frame * 2
    if gain == 1.0:
        # 不重叠时直接铺。重叠就饱和相加。
        region = dst[dest_at:dest_at + len(chunk)]
        if all(sample == 0 for sample in region):
            dst[dest_at:dest_at + len(chunk)] = chunk
            return
    for index, sample in enumerate(chunk):
        mixed = int(dst[dest_at + index] + sample * gain)
        if mixed > 32767:
            mixed = 32767
        elif mixed < -32768:
            mixed = -32768
        dst[dest_at + index] = mixed


def build_bed(duration: float, segments: list[dict], wavs: dict[str, array.array], gain: float) -> array.array:
    total = max(1, int(math.ceil(duration * RATE)))
    bed = array.array("h", bytes(total * 2 * 2))
    for segment in segments:
        src = wavs.get(segment["src"])
        if src is None:
            log(f"跳过没有音轨文件的片段：{segment['src']}")
            continue
        mix_at(
            bed,
            src,
            int(segment["wall"] * RATE),
            int(segment["media"] * RATE),
            int(segment["dur"] * RATE),
            gain,
        )
    return bed


def speech_text(text: str) -> str:
    return " ".join(part.strip() for part in text.splitlines() if part.strip())


def synthesize_polly(text: str, dest: Path, region: str) -> None:
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("加了 --polly，但没有 boto3。先 python3 -m pip install boto3") from exc
    client = boto3.client("polly", region_name=region)
    response = client.synthesize_speech(
        Engine="neural",
        LanguageCode="cmn-CN",
        OutputFormat="mp3",
        Text=text,
        VoiceId="Zhiyu",
    )
    dest.write_bytes(response["AudioStream"].read())


def fit_voice(src_wav: Path, dest_wav: Path, slot: float) -> Path:
    length = probe_duration(src_wav)
    if slot <= 0.2 or length <= slot + 0.12:
        return src_wav
    factor = length / slot
    tempos = []
    remaining = factor
    while remaining > 2.0:
        tempos.append("atempo=2.0")
        remaining /= 2.0
    tempos.append(f"atempo={remaining:.4f}")
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src_wav),
        "-filter:a", ",".join(tempos),
        "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le",
        str(dest_wav),
    ])
    return dest_wav


def polly_voices(entries: list[tuple[float, float, str]], workdir: Path, region: str) -> list[tuple[float, array.array]]:
    voices = []
    for index, (start, end, text) in enumerate(entries):
        spoken = speech_text(text)
        if not spoken:
            continue
        mp3 = workdir / f"polly-{index:02d}.mp3"
        wav = workdir / f"polly-{index:02d}.wav"
        fitted = workdir / f"polly-{index:02d}-fit.wav"
        log(f"Polly {index + 1}/{len(entries)} {spoken[:24]}")
        synthesize_polly(spoken, mp3, region)
        extract_wav(mp3, wav)
        chosen = fit_voice(wav, fitted, end - start)
        voices.append((start, load_wav(chosen)))
    return voices


def absolute_url(base: str, src: str) -> str:
    return urllib.parse.urljoin(base.rstrip("/") + "/", src)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "deepcourt-demo"})
    with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temporary.replace(dest)


class Demo:
    def __init__(self, page, base: str, clip: str, workdir: Path, t_page0: float):
        self.page = page
        self.base = base
        self.clip = clip
        self.workdir = workdir
        self.t_page0 = t_page0
        self.info: dict = {}
        self.sync = {"perf0": 0.0, "py_attach": t_page0}
        self.downloads: dict[str, Path] = {}
        self.download_error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self.shot_started = 0.0
        self.saved_cues: list[dict] = []
        self.cues_saved = False

    def now(self) -> float:
        return time.monotonic() - self.t_page0

    def hold_until(self, offset: float) -> None:
        target = self.shot_started + offset
        while True:
            left = target - self.now()
            if left <= 0.02:
                return
            self.page.wait_for_timeout(min(200, int(left * 1000)))

    def snap(self, name: str) -> None:
        folder = self.workdir / "shots"
        folder.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(folder / f"{name}.png"))

    def media_time(self) -> float:
        return float(self.page.evaluate(
            "() => { const v = document.querySelector('#reel'); return v ? (v.currentTime || 0) : 0; }"
        ))

    def ended(self) -> bool:
        return bool(self.page.evaluate(
            "() => { const v = document.querySelector('#reel'); return !v || v.ended; }"
        ))

    def show_controls(self) -> None:
        box = self.page.locator("#reel").bounding_box()
        if not box:
            return
        x = box["x"] + box["width"] * 0.55
        y = box["y"] + box["height"] - 12
        self.page.mouse.move(x, y)
        self.page.mouse.move(x + 3, y)

    def seek(self, target: float) -> None:
        self.page.evaluate(
            """(t) => new Promise((resolve) => {
              const v = document.querySelector('#reel');
              if (!v) { resolve(0); return; }
              const finish = () => resolve(v.currentTime || 0);
              if (Math.abs((v.currentTime || 0) - t) < 0.08) { finish(); return; }
              const timer = setTimeout(finish, 2500);
              v.addEventListener('seeked', () => { clearTimeout(timer); finish(); }, { once: true });
              try { v.currentTime = t; } catch (error) { clearTimeout(timer); finish(); }
            })""",
            target,
        )

    def play(self) -> None:
        result = self.page.evaluate(
            """async () => {
              const v = document.querySelector('#reel');
              if (!v) return {ok: false};
              try {
                await v.play();
                return {ok: true, muted: v.muted};
              } catch (error) {
                v.muted = true;
                await v.play();
                return {ok: false, muted: true, error: String(error)};
              }
            }"""
        )
        if result and result.get("muted") and not result.get("ok", True):
            log("浏览器拦住了有声自动播放，已静音继续。音轨仍从成片文件拼。")

    def pause(self) -> None:
        self.page.evaluate("() => { const v = document.querySelector('#reel'); if (v) v.pause(); }")

    def play_until(self, offset: float, limit: float | None) -> None:
        """从当前画面播到镜头内 offset，或播到 limit（避免提前触发定格）。"""
        while self.now() < self.shot_started + offset:
            if limit is not None and self.media_time() >= limit:
                break
            if self.ended():
                break
            if int((self.now() - self.shot_started) * 2) % 2 == 0:
                self.show_controls()
            self.page.wait_for_timeout(200)
        self.pause()

    def safe_limit(self) -> float | None:
        goal = self.info.get("goal")
        if isinstance(goal, (int, float)):
            return max(0.0, float(goal) - 1.5)
        duration = self.info.get("duration")
        if isinstance(duration, (int, float)) and duration > 0.5:
            return float(duration) - 0.3
        return None

    def scroll_to_text(self, text: str, margin: int = 140) -> None:
        found = self.page.evaluate(
            """([text, margin]) => {
              const nodes = [...document.querySelectorAll('h1, h2, p, li, tr, td')];
              const el = nodes.find((node) => (node.innerText || '').includes(text));
              if (!el) return false;
              const y = el.getBoundingClientRect().top + window.scrollY - margin;
              const start = window.scrollY;
              const target = Math.max(0, y);
              const steps = 8;
              let step = 0;
              return new Promise((resolve) => {
                const tick = () => {
                  step += 1;
                  window.scrollTo(0, start + (target - start) * step / steps);
                  if (step >= steps) resolve(true);
                  else setTimeout(tick, 40);
                };
                tick();
              });
            }""",
            [text, margin],
        )
        if not found:
            log(f"页面上没找到「{text}」，停在原处")

    def attach_audio_log(self) -> None:
        perf0 = self.page.evaluate(
            """() => {
              window.__demoT0 = performance.now();
              window.__demoCues = [];
              const video = document.querySelector('#reel');
              if (!video) return window.__demoT0;
              const mark = (type) => {
                window.__demoCues.push({
                  perf: performance.now(),
                  type,
                  media: video.currentTime || 0,
                  src: video.currentSrc || '',
                });
              };
              ['play', 'pause', 'seeked', 'ended'].forEach((name) => {
                video.addEventListener(name, () => mark(name), true);
              });
              return window.__demoT0;
            }"""
        )
        self.sync = {"perf0": float(perf0), "py_attach": time.monotonic()}

    def harvest_cues(self) -> list[dict]:
        raw = self.page.evaluate("() => window.__demoCues || []")
        perf0 = self.sync["perf0"]
        attached = self.sync["py_attach"] - self.t_page0
        cues = []
        for item in raw:
            wall = (float(item["perf"]) - perf0) / 1000 + attached
            cues.append({
                "wall": wall,
                "type": item["type"],
                "media": float(item.get("media") or 0),
                "src": item.get("src") or "",
            })
        return cues

    def save_cues(self) -> None:
        """离开播放器之前把音轨事件取出。/readme 是一次新的文档，原来的记录会丢。"""
        if self.cues_saved:
            return
        try:
            if self.page.locator("#reel").count():
                self.saved_cues = self.harvest_cues()
                self.cues_saved = True
        except Exception as exc:
            log(f"读播放记录失败：{exc}")

    def start_downloads(self) -> None:
        urls = {}
        tracks = self.info.get("tracks") or {}
        for key, src in tracks.items():
            if src:
                urls[absolute_url(self.base, src)] = key
        video = self.info.get("video")
        if video:
            urls.setdefault(absolute_url(self.base, video), "video")
        self._thread = threading.Thread(target=self._download_all, args=(urls,), daemon=True)
        self._thread.start()

    def _download_all(self, urls: dict[str, str]) -> None:
        try:
            for url in urls:
                name = Path(urllib.parse.urlparse(url).path).name or "track.mp4"
                dest = self.workdir / "media" / name
                if not dest.exists() or dest.stat().st_size < 1000:
                    log(f"下载音轨 {name}")
                    download(url, dest)
                self.downloads[url] = dest
        except Exception as exc:  # 记下来，录完再抛，免得页面线程半路死掉
            self.download_error = exc

    def wait_downloads(self) -> None:
        if self._thread:
            self._thread.join(timeout=180)
        if self.download_error:
            raise self.download_error


def wait_ready(demo: Demo) -> None:
    demo.page.wait_for_selector("#reel", timeout=30000)
    demo.page.wait_for_function(
        """(clip) => {
          const clips = window.DEEPCOURT_CLIPS || {};
          const video = document.querySelector('#reel');
          const selected = document.querySelector('#clip-switch .clip.on');
          return Boolean(clips[clip] && video && video.readyState >= 1 && selected);
        }""",
        arg=demo.clip,
        timeout=45000,
    )
    demo.page.wait_for_timeout(400)


def read_pack(demo: Demo) -> None:
    info = demo.page.evaluate(
        """(clip) => {
          const clips = window.DEEPCOURT_CLIPS || {};
          const pack = clips[clip];
          if (!pack) return {ok: false, keys: Object.keys(clips)};
          const shot = window.DeepFreeze ? (window.DeepFreeze.candidates(pack)[0] || null) : null;
          const goal = shot ? window.DeepFreeze.goalTime(pack, shot) : null;
          const video = document.querySelector('#reel');
          return {
            ok: true,
            title: pack.title || clip,
            duration: video && video.duration ? video.duration : (pack.duration || 0),
            goal,
            release: shot && shot.release ? shot.release.t : null,
            events: (pack.events || []).map((item) => ({
              t: item.t, label: item.label || '', timecode: item.timecode || '', eventType: item.eventType || '',
            })),
            tracks: pack.tracks || {},
            video: pack.video || '',
            hasFloor: !!(pack.onCourt && Object.keys(pack.onCourt).length),
          };
        }""",
        demo.clip,
    )
    if not info.get("ok"):
        keys = "、".join(info.get("keys") or []) or "（没有）"
        raise SystemExit(f"播放器上没有 clip={demo.clip}。现在有：{keys}")
    demo.info = info
    en = demo.page.locator("#lang-switch button[data-lang='en']")
    demo.info["hasEn"] = en.count() > 0 and en.is_enabled()
    log(f"片子 {info['title']} 时长 {info['duration']:.2f}s 进球触发 {info.get('goal')}")


def action_open(demo: Demo) -> None:
    url = f"{demo.base.rstrip('/')}/?clip={urllib.parse.quote(demo.clip)}&lang=zh"
    log(f"打开 {url}")
    demo.page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_ready(demo)
    read_pack(demo)
    demo.attach_audio_log()
    demo.start_downloads()
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    demo.snap("open")


def action_play(demo: Demo) -> None:
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    demo.seek(0)
    demo.play()
    demo.page.wait_for_timeout(600)
    demo.snap("play")
    demo.show_controls()
    demo.play_until(18, demo.safe_limit())


def pick_events(events: list[dict]) -> list[dict]:
    if not events:
        return []
    shot = next((item for item in events if item.get("eventType") == "shot"), events[-1])
    middle = events[len(events) // 2]
    chosen = []
    for item in (events[0], middle, shot):
        if all(abs(float(item["t"]) - float(prev["t"])) > 0.4 for prev in chosen):
            chosen.append(item)
    return chosen


def action_timeline(demo: Demo) -> None:
    demo.pause()
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    demo.show_controls()
    buttons = demo.page.locator("button.event")
    count = buttons.count()
    events = pick_events(demo.info.get("events") or [])
    slots = max(1, count if count else len(events))
    slot = 8 / slots
    if count:
        indexes = []
        for index in (0, count // 2, count - 1):
            if index not in indexes:
                indexes.append(index)
        for nth, index in enumerate(indexes):
            buttons.nth(index).scroll_into_view_if_needed()
            buttons.nth(index).click()
            demo.show_controls()
            demo.hold_until(slot * (nth + 1))
        demo.snap("timeline")
        return
    if not events:
        log("这支没有事件点，时间线只留播放头")
        demo.snap("timeline")
        return
    for nth, event in enumerate(events):
        log(f"时间线 {event.get('timecode') or ''} {event.get('label') or ''} @ {float(event['t']):.2f}s")
        demo.seek(float(event["t"]))
        demo.show_controls()
        if nth == len(events) - 1:
            demo.snap("timeline")
        demo.hold_until(slot * (nth + 1))


def action_freeze(demo: Demo) -> None:
    goal = demo.info.get("goal")
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    if not isinstance(goal, (int, float)):
        log("这支没有带出手帧的进球，跳过定格")
        demo.snap("freeze")
        return
    demo.seek(max(0.0, float(goal) - 5.5))
    demo.play()
    try:
        demo.page.wait_for_function(
            """() => {
              const card = document.querySelector('#freeze');
              return card && !card.hidden;
            }""",
            timeout=18000,
        )
    except Exception:
        log("定格没在窗口里出现，把播放头推到触发点再试一次")
        demo.seek(float(goal) + 0.05)
        demo.play()
        demo.page.wait_for_function(
            """() => {
              const card = document.querySelector('#freeze');
              return card && !card.hidden;
            }""",
            timeout=6000,
        )
    demo.page.wait_for_timeout(400)
    demo.snap("freeze")
    demo.page.wait_for_function(
        """() => {
          const card = document.querySelector('#freeze');
          return !card || card.hidden;
        }""",
        timeout=8000,
    )


def action_lang(demo: Demo) -> None:
    demo.pause()
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    if not demo.info.get("hasEn"):
        log("这支没有可切的英文音轨，停在当前音轨")
        demo.snap("lang")
        return
    limit = demo.safe_limit()
    start = 0.0 if limit is None else max(0.0, float(limit) - 4.5)

    def play_bounded(until_offset: float) -> None:
        demo.seek(start)
        demo.play()
        while demo.now() < demo.shot_started + until_offset:
            if limit is not None and demo.media_time() >= float(limit) - 0.05:
                break
            if demo.ended():
                break
            demo.show_controls()
            demo.page.wait_for_timeout(150)
        demo.pause()

    switch_lang(demo, "en")
    demo.snap("lang-en")
    play_bounded(5)
    switch_lang(demo, "zh")
    demo.snap("lang-zh")
    play_bounded(14)


def switch_lang(demo: Demo, lang: str) -> None:
    demo.page.locator(f"#lang-switch button[data-lang='{lang}']").click()
    demo.page.wait_for_function(
        """(lang) => {
          const video = document.querySelector('#reel');
          const button = document.querySelector(`#lang-switch button[data-lang="${lang}"]`);
          return video && video.currentSrc.includes('-' + lang)
            && video.readyState >= 2
            && button && button.classList.contains('on');
        }""",
        arg=lang,
        timeout=20000,
    )
    demo.page.wait_for_timeout(350)


def action_floor(demo: Demo) -> None:
    hidden = demo.page.evaluate("() => { const el = document.querySelector('#floor'); return !el || el.hidden; }")
    if hidden:
        log("这支没有场上名单")
        demo.snap("floor")
        return
    demo.page.evaluate(
        """() => {
          const el = document.querySelector('#floor');
          const y = el.getBoundingClientRect().top + window.scrollY - 48;
          window.scrollTo(0, Math.max(0, y));
        }"""
    )
    demo.page.wait_for_timeout(300)
    demo.snap("floor")


def action_readme(demo: Demo) -> None:
    demo.save_cues()
    demo.page.goto(demo.base.rstrip("/") + "/readme", wait_until="domcontentloaded", timeout=60000)
    demo.page.wait_for_selector("h1", timeout=20000)
    demo.page.evaluate("() => window.scrollTo(0, 0)")
    demo.snap("readme")
    demo.hold_until(5)
    demo.scroll_to_text("数字从哪来")
    demo.hold_until(10)
    demo.scroll_to_text("数据管道")
    demo.snap("readme-pipe")


def action_aws(demo: Demo) -> None:
    demo.scroll_to_text("收片、任务", margin=180)
    demo.snap("aws")
    demo.hold_until(5)
    demo.scroll_to_text("RapidOCR", margin=160)
    demo.hold_until(10)
    demo.scroll_to_text("Bedrock Qwen3 VL", margin=160)
    demo.hold_until(24)
    demo.scroll_to_text("MediaConvert", margin=160)
    demo.snap("aws-end")


ACTIONS = {
    "open": action_open,
    "play": action_play,
    "timeline": action_timeline,
    "freeze": action_freeze,
    "lang": action_lang,
    "floor": action_floor,
    "readme": action_readme,
    "aws": action_aws,
}


def run_shots(demo: Demo, until: str) -> list[dict]:
    done = []
    for shot in SHOTS:
        demo.shot_started = demo.now()
        log(f"镜头 {shot['id']} {shot['title']} @ {demo.shot_started:.1f}s")
        ACTIONS[shot["id"]](demo)
        demo.hold_until(shot["seconds"])
        end = demo.now()
        done.append({
            "id": shot["id"],
            "title": shot["title"],
            "seconds": shot["seconds"],
            "cues": shot["cues"],
            "start": demo.shot_started,
            "end": end,
        })
        log(f"  结束 {end:.1f}s（本镜 {end - demo.shot_started:.1f}s）")
        if until and until == shot["id"]:
            break
    return done


def launch(playwright):
    errors = []
    for options in ({"channel": "chrome"}, {}):
        try:
            return playwright.chromium.launch(headless=True, args=CHROME_ARGS, **options)
        except Exception as exc:
            errors.append(str(exc))
    raise SystemExit("打不开 Chrome 或 Chromium。\n" + "\n".join(errors))


def record_pages(base: str, clip: str, workdir: Path, until: str) -> tuple[list[dict], list[dict], Path]:
    from playwright.sync_api import sync_playwright

    raw_dir = workdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    done: list[dict] = []
    cues: list[dict] = []
    demo: Demo | None = None
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(raw_dir),
            record_video_size={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(20000)
        t_page0 = time.monotonic()
        demo = Demo(page, base, clip, workdir, t_page0)
        try:
            done = run_shots(demo, until)
        finally:
            demo.save_cues()
            cues = demo.saved_cues
            context.close()
            browser.close()
        demo.wait_downloads()
    webms = sorted(raw_dir.glob("*.webm"), key=lambda path: path.stat().st_mtime)
    if not webms:
        raise RuntimeError("没有录到画面")
    return done, cues, webms[-1]


def wav_for_sources(demo_downloads: dict[str, Path], workdir: Path) -> dict[str, array.array]:
    wavs = {}
    for url, mp4 in demo_downloads.items():
        wav_path = workdir / "media" / (mp4.stem + ".wav")
        if not wav_path.exists():
            extract_wav(mp4, wav_path)
        wavs[url] = load_wav(wav_path)
    return wavs


def compose_audio(
    webm: Path,
    segments: list[dict],
    downloads: dict[str, Path],
    entries: list[tuple[float, float, str]],
    workdir: Path,
    polly: bool,
    region: str,
) -> Path:
    duration = probe_duration(webm)
    wavs = wav_for_sources(downloads, workdir)
    # currentSrc 是绝对地址。对不上时用文件名再试一次。
    by_name = {}
    for url, samples in wavs.items():
        by_name[Path(urllib.parse.urlparse(url).path).name] = samples
    resolved = {}
    for segment in segments:
        src = segment["src"]
        if src in wavs:
            resolved[src] = wavs[src]
            continue
        name = Path(urllib.parse.urlparse(src).path).name
        if name in by_name:
            resolved[src] = by_name[name]
    if segments and not resolved:
        names = ", ".join(sorted({Path(urllib.parse.urlparse(item["src"]).path).name for item in segments}))
        raise RuntimeError(f"有播放记录，但没有对上音轨文件：{names}")
    page_gain = 0.28 if polly else 1.0
    bed = build_bed(duration + 0.4, segments, resolved, page_gain)
    if polly:
        for start, samples in polly_voices(entries, workdir, region):
            mix_at(bed, samples, int(start * RATE), 0, len(samples) // 2, 0.92)
    out = workdir / "bed.wav"
    write_wav(out, bed)
    return out


def burn(webm: Path, wav_path: Path, srt: Path, dest: Path, font: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg 把 SRT 收成 PlayRes 384×288 再放大到 1080p，字号大约乘 3.75。
    # 11 号在画面上约 40px，落在播放器下方，不挡住视频时间线。
    style = (
        f"FontName={font},FontSize=11,PrimaryColour=&H00E2EDF3,"
        "OutlineColour=&HC014110E,BorderStyle=3,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=14"
    )
    video_filter = f"subtitles={escape_filter_path(srt)}:charenc=UTF-8:force_style='{style}'"
    for crf in (28, 32, 36):
        temporary = dest.with_suffix(f".crf{crf}.mp4")
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(webm),
            "-i", str(wav_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", video_filter,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(temporary),
        ])
        size = temporary.stat().st_size
        log(f"编码 crf {crf}：{size / 1024 / 1024:.1f} MB")
        if dest.exists():
            dest.unlink()
        temporary.replace(dest)
        if size <= MAX_BYTES:
            return
    log("成片仍大于 50MB，已用最高压缩留下这一版")


def write_timeline(path: Path, done: list[dict], cues: list[dict], segments: list[dict]) -> None:
    payload = {"shots": done, "cues": cues, "segments": segments}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def record(args: argparse.Namespace) -> int:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("需要 ffmpeg 和 ffprobe")
    workdir = Path(args.workdir) if args.workdir else Path(args.out).parent / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    font = choose_font()
    log(f"字幕字体 {font}")
    done, cues, webm = record_pages(args.base_url, args.clip, workdir, args.until)
    entries = realize_cues(done)
    srt_text = render_srt(entries)
    srt_path = Path(args.out).with_suffix(".srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(srt_text, encoding="utf-8")
    (workdir / "narration.srt").write_text(srt_text, encoding="utf-8")
    segments = segments_from_cues(cues)
    log(f"音轨片段 {len(segments)} 条")
    for segment in segments:
        name = Path(urllib.parse.urlparse(segment["src"]).path).name
        log(f"  {segment['wall']:.1f}s +{segment['dur']:.1f}s 媒体 {segment['media']:.1f}s {name}")
    media = workdir / "media"
    write_timeline(workdir / "timeline.json", done, cues, segments)
    named = {}
    if media.exists():
        for mp4 in media.glob("*.mp4"):
            named[mp4.name] = mp4
    fake_downloads = {args.base_url.rstrip("/") + "/assets/" + name: path for name, path in named.items()}
    wav_path = compose_audio(webm, segments, fake_downloads, entries, workdir, args.polly, args.region)
    burn(webm, wav_path, srt_path, Path(args.out), font)
    duration = probe_duration(Path(args.out))
    size = Path(args.out).stat().st_size
    log(f"成片 {args.out}  {duration:.1f}s  {size / 1024 / 1024:.1f} MB")
    log(f"字幕 {srt_path}")
    if not args.until and not (90 <= duration <= 120):
        log("时长不在 90–120 秒。检查页面加载是否卡住，或这支片子的进球点特别晚。")
        return 2
    return 0


def run_check() -> int:
    total = planned_total()
    assert abs(total - 120) < 0.1, total
    done = []
    cursor = 0.0
    for shot in SHOTS:
        done.append({**shot, "start": cursor, "end": cursor + shot["seconds"]})
        cursor += shot["seconds"]
    entries = realize_cues(done)
    text = render_srt(entries)
    assert "Team 04 · Deep Court" in text
    assert "Qwen3 VL 看多帧" in text
    assert "Rekognition 算第二票" in text
    assert "只认场上十个人，一个名字一条轨迹" in text
    assert "官方逐回合先钉得分人和助攻人" in text
    assert "Qwen 调失败才用 Textract" in text
    assert "Agent detecting" in text
    assert text.strip().endswith("密钥仍是空占位。")
    cues = [
        {"wall": 1.0, "type": "play", "media": 2.0, "src": "zh.mp4"},
        {"wall": 4.0, "type": "pause", "media": 5.0, "src": "zh.mp4"},
        {"wall": 4.2, "type": "seeked", "media": 10.0, "src": "zh.mp4"},
        {"wall": 5.0, "type": "play", "media": 10.0, "src": "en.mp4"},
        {"wall": 6.5, "type": "seeked", "media": 12.0, "src": "en.mp4"},
        {"wall": 8.0, "type": "ended", "media": 13.5, "src": "en.mp4"},
    ]
    segments = segments_from_cues(cues)
    assert len(segments) == 3, segments
    assert abs(segments[0]["dur"] - 3.0) < 1e-6
    assert abs(segments[1]["media"] - 10.0) < 1e-6
    assert abs(segments[2]["wall"] - 6.5) < 1e-6 and abs(segments[2]["dur"] - 1.5) < 1e-6
    tone = array.array("h", [0] * RATE * 2)
    for frame in range(RATE):
        sample = int(12000 * math.sin(2 * math.pi * 440 * frame / RATE))
        tone[frame * 2] = sample
        tone[frame * 2 + 1] = sample
    bed = array.array("h", bytes(RATE * 2 * 2 * 2))
    mix_at(bed, tone, RATE // 2, 0, RATE // 2, 1.0)
    assert all(sample == 0 for sample in bed[:RATE])
    assert any(sample != 0 for sample in bed[RATE:RATE * 2])
    log(f"--check 通过。计划时长 {total:.0f}s，字幕 {len(entries)} 条，字体 {choose_font()}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="录 Deep Court 演示视频并烧中文字幕")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--clip", default="flagg", help="播放器 ?clip= 的值，见 web/replay/clips.js")
    parser.add_argument("--polly", action="store_true", help="用 Polly Neural 知语按句合成并叠上音轨")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out" / "demo.mp4"))
    parser.add_argument("--workdir", default="", help="中间文件目录，默认为成片旁边的 work/")
    parser.add_argument("--until", default="", help="调试：录到这个镜头 id 就停")
    parser.add_argument("--check", action="store_true", help="不打开浏览器，检查字幕和音轨拼接")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        return run_check()
    return record(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
