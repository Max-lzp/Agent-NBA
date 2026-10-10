"""英文原声和中文解说两条音轨。

    python3 -m pipeline.present.tracks --clip flagg --clip amen

英文音轨是源片声音，不做处理。
中文音轨先用 Amazon Transcribe 转写英文解说，再用 Amazon Translate 译成中文，
最后用 Amazon Polly Neural 的知语合成。普通话在 Polly 里只有知语这一条；
Generative 没有普通话，Neural 比 Standard 更接近人声。

这两条训练片的现场声和原解说叠在一起，声道又几乎是单声道，分不开。
中文音轨因此只留合成人声，原解说整段拿掉。两条都装进成片画面，页面上切换。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "pipeline" / "events" / "out"
WEB = REPO / "web"
WORK = Path(__file__).resolve().parent / "work"
AUDIO = Path(__file__).resolve().parent / "audio"
REGION = "us-east-1"
STACK = "Team04DeepCourt"
VOICE_ID = "Zhiyu"
ENGINE = "neural"
VOICE_NOTE = "中文音轨是英文解说的译文，用 Amazon Polly Neural 知语合成，不含原解说。英文音轨是原声。"

# 转写常把人名和术语听错。先改英文，再交给 Translate，避免译成「做饭」「开车」。
WORD_FIX = {
    "Shengoun": "Sengun",
    "Flag": "Flagg",
    "isoing": "one-on-one",
    "KD": "Durant",
    "um": "",
}
PHRASES = (
    ("5 on the 24", "five seconds left on the shot clock"),
    ("back to 4", "the lead is back to four"),
    ("foul line jump", "jumper at the free throw line"),
    ("cooking", "playing very well"),
    ("in Thompson", "Thompson"),
    ("five seconds left on the shot clock drives inside", "with five seconds left on the shot clock, drives to the basket"),
    ("drives inside", "drives to the basket"),
    ("gone hard", "played very hard"),
    ("held his ground well", "defended well"),
    ("give it up to", "pass it to"),
    ("out on the perimeter", "outside"),
    ("off one leg", "off one foot"),
    ("one-on-one against", "in a one-on-one against"),
    ("Here's", "Here comes"),
    ("layup is good", "layup goes in"),
)
# 译文里和页面不一致的叫法。长的先替换。
REPLACEMENTS = (
    ("库珀·弗拉格", "弗拉格"),
    ("库珀弗拉格", "弗拉格"),
    ("库珀", "弗拉格"),
    ("达拉斯独行侠", "独行侠"),
    ("达拉斯小牛队", "独行侠"),
    ("达拉斯小牛", "独行侠"),
    ("小牛队", "独行侠"),
    ("小牛", "独行侠"),
    ("休斯顿火箭队", "火箭"),
    ("休斯顿火箭", "火箭"),
    ("火箭队", "火箭"),
    ("凯文·杜兰特", "杜兰特"),
    ("凯文杜兰特", "杜兰特"),
    ("阿门·汤普森", "阿门"),
    ("阿蒙·汤普森", "阿门"),
    ("阿芒·汤普森", "阿门"),
    ("阿门汤普森", "阿门"),
    ("汤普森", "阿门"),
    ("申贡", "申京"),
    ("森贡", "申京"),
    ("森根", "申京"),
    ("埃森", "伊森"),
    ("艾森", "伊森"),
    ("在这里做饭", "手感正热"),
    ("做饭", "手感正热"),
    ("着火了", "手感正热"),
    ("着火", "手感正热"),
    ("开车进去", "杀进内线"),
    ("开车", "突破"),
    ("射门", "出手"),
    ("犯规线", "罚球线"),
    ("一条腿", "单脚"),
    ("挺身而出", "一对一"),
    ("Sengun", "申京"),
    ("Flagg", "弗拉格"),
    ("驱动器", "内线"),
    ("车手", "突破"),
    ("跳线", "跳投"),
    ("单腿", "单脚"),
    ("出手时钟", "进攻时间"),
    ("过得很努力", "打得很猛"),
    ("保持了良好的立场", "防住了"),
    ("良好的立场", "防守"),
)


def sentences_from_items(items: list[dict]) -> list[dict]:
    """把 Transcribe 的词切成句，记下每句的起止时间。"""
    sentences = []
    words: list[str] = []
    start = None
    end = None
    for item in items:
        content = ((item.get("alternatives") or [{}])[0].get("content") or "").strip()
        if not content:
            continue
        if item.get("type") == "punctuation":
            if words and start is not None and content in ".?!。！？":
                sentences.append({"start": start, "end": end, "text": " ".join(words)})
                words, start, end = [], None, None
            continue
        if item.get("start_time") is None:
            continue
        if start is None:
            start = float(item["start_time"])
        end = float(item.get("end_time") or item["start_time"])
        words.append(content)
    if words and start is not None:
        sentences.append({"start": start, "end": end, "text": " ".join(words)})
    return sentences


def prepare_sentences(items: list[dict]) -> list[dict]:
    """整理转写：去掉片段开头被切断的一个词，再按句对齐时间。"""
    tokens = []
    for item in items:
        content = ((item.get("alternatives") or [{}])[0].get("content") or "").strip()
        if not content:
            continue
        if item.get("type") == "punctuation":
            tokens.append({"kind": "punct", "text": content})
            continue
        if item.get("start_time") is None:
            continue
        text = WORD_FIX.get(content, content)
        if not text:
            continue
        tokens.append({
            "kind": "word",
            "text": text,
            "start": float(item["start_time"]),
            "end": float(item.get("end_time") or item["start_time"]),
        })
    spoken = [token for token in tokens if token["kind"] == "word"]
    if len(spoken) >= 2 and spoken[1]["start"] - spoken[0]["end"] > 1.2:
        dropped = False
        kept = []
        for token in tokens:
            if not dropped and token["kind"] == "word":
                dropped = True
                continue
            kept.append(token)
        tokens = kept
    sentences = sentences_from_tokens(tokens)
    prepared = []
    for sentence in sentences:
        text = clarify_english(sentence["text"])
        if text:
            prepared.append({**sentence, "text": text})
    return prepared


def sentences_from_tokens(tokens: list[dict]) -> list[dict]:
    sentences = []
    words: list[str] = []
    start = None
    end = None
    for token in tokens:
        if token["kind"] == "punct":
            if words and start is not None and token["text"] in ".?!。！？":
                sentences.append({"start": start, "end": end, "text": " ".join(words)})
                words, start, end = [], None, None
            continue
        if start is None:
            start = token["start"]
        end = token["end"]
        words.append(token["text"])
    if words and start is not None:
        sentences.append({"start": start, "end": end, "text": " ".join(words)})
    return sentences


def clarify_english(text: str) -> str:
    for src, dst in PHRASES:
        text = text.replace(src, dst)
    text = re.sub(r"\b([A-Za-z]+)\s+\1\b", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if text and text[-1] not in ".!?":
        text += "."
    return text


# Translate 会把 drives 译成「车手」「驱动器」，把 one foot 译成「一英尺」。
# 这几句训练片按转写的意思写成解说口吻；对不上的句子仍走 Translate。
COMMENTARY_ZH = {
    "7 assists for Sengun 21 points for Thompson the lead is back to four.":
        "申京已经 7 次助攻，阿门 21 分，分差回到 4 分。",
    "Flagg with five seconds left on the shot clock, drives to the basket left-handed layup goes in.":
        "弗拉格，进攻时间还剩 5 秒，杀进内线，左手上篮打进。",
    "Cooper's playing very well here in the 4th quarter.":
        "第四节，弗拉格手感正热。",
    "We gotta see that one again.":
        "这球得再看一遍。",
    "He has played very hard tonight.":
        "他今晚打得很猛。",
    "Eason in a one-on-one against Marshall drives Marshall defended well so he forced him to pass it to Durant outside.":
        "伊森一对一打马绍尔，杀进去，马绍尔防住了，逼他把球交到外线的杜兰特。",
    "Here comes Thompson off one foot at the end of the shot clock hits a jumper at the free throw line.":
        "进攻时间结束前，阿门单脚起跳，罚球线跳投命中。",
}


def prefer_broadcast(english: str, machine: str) -> str:
    return COMMENTARY_ZH.get(" ".join(english.split()), machine)


def polish_zh(text: str) -> str:
    cleaned = " ".join((text or "").split())
    for src, dst in REPLACEMENTS:
        if src != dst:
            cleaned = cleaned.replace(src, dst)
    if cleaned and cleaned[-1] not in "。！？":
        cleaned += "。"
    return cleaned


def playback_rate(audio_s: float, slot_s: float) -> float:
    """合成声比画面剩下的时间长时，最多加快到 1.35，避免把句尾剪掉。"""
    if audio_s <= 0 or slot_s <= 0 or audio_s <= slot_s:
        return 1.0
    return min(audio_s / slot_s, 1.35)


def voice_filter(start_s: float, rate: float, duration_s: float) -> str:
    parts = ["aformat=channel_layouts=stereo"]
    if rate > 1.01:
        parts.append(f"atempo={rate:.4f}")
    delay = int(round(max(0.0, start_s) * 1000))
    if delay > 0:
        parts.append(f"adelay={delay}|{delay}")
    parts.append(f"apad=whole_dur={duration_s:.3f}")
    parts.append(f"atrim=end={duration_s:.3f}")
    return ",".join(parts)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _assert_playable(path: Path, duration_s: float) -> None:
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time",
        "-of", "default=nw=1", str(path),
    ], text=True)
    if "codec_type=audio" not in raw:
        raise SystemExit(f"{path.name} 没有音轨")
    for line in raw.splitlines():
        if line.startswith("start_time=") and line != "start_time=N/A":
            start = float(line.split("=", 1)[1])
            if start > duration_s:
                raise SystemExit(f"{path.name} 音轨时间戳异常：{start}")


def media_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        text=True,
    )
    return float(out.strip())


def _source_video(clip: str) -> Path:
    stream = json.loads((OUT / f"{clip}.json").read_text(encoding="utf-8"))
    src = (stream.get("video") or {}).get("source") or ""
    path = Path(src)
    if not path.is_absolute():
        path = (REPO / src).resolve()
    return path


def _aws_bin() -> str:
    found = shutil_which("aws")
    fallback = Path.home() / ".local" / "bin" / "aws"
    if found:
        return found
    if fallback.exists():
        return str(fallback)
    raise SystemExit("找不到 aws 命令")


def _aws(args: list[str], *, json_out: bool = True) -> str:
    cmd = [_aws_bin(), *args, "--profile", "nba", "--region", REGION]
    if json_out:
        cmd.extend(["--output", "json"])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(detail or "aws 命令失败")
    return result.stdout


def _media_bucket() -> str:
    raw = _aws([
        "cloudformation", "describe-stacks", "--stack-name", STACK,
        "--query", "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue",
    ])
    names = json.loads(raw)
    name = names[0] if names else ""
    if not name:
        raise SystemExit(f"栈 {STACK} 没有 MediaBucketName")
    return name


def _transcribe(clip: str, wav: Path, bucket: str) -> dict:
    key = f"commentary/asr/{clip}.wav"
    _aws([
        "s3", "cp", str(wav), f"s3://{bucket}/{key}",
        "--content-type", "audio/wav",
    ], json_out=False)
    job = f"deepcourt-{clip}-{int(time.time())}"
    out_key = f"commentary/asr/{job}.json"
    _aws([
        "transcribe", "start-transcription-job",
        "--transcription-job-name", job,
        "--language-code", "en-US",
        "--media-format", "wav",
        "--media", f"MediaFileUri=s3://{bucket}/{key}",
        "--output-bucket-name", bucket,
        "--output-key", out_key,
    ])
    deadline = time.time() + 180
    status = "IN_PROGRESS"
    reason = ""
    while time.time() < deadline:
        got = json.loads(_aws([
            "transcribe", "get-transcription-job",
            "--transcription-job-name", job,
        ]))["TranscriptionJob"]
        status = got["TranscriptionJobStatus"]
        reason = got.get("FailureReason") or ""
        if status in ("COMPLETED", "FAILED"):
            break
        time.sleep(3)
    if status != "COMPLETED":
        raise SystemExit(f"{clip} 转写没有完成：{status} {reason}")
    local = WORK / out_key.replace("/", "-")
    _aws(["s3", "cp", f"s3://{bucket}/{out_key}", str(local)], json_out=False)
    return json.loads(local.read_text(encoding="utf-8"))


# 术语表可选。没设 TRANSLATE_TERMINOLOGY 时，请求体和现在一样。
# 表不存在或没有权限时去掉术语表再译一次；这次进程里随后的句子也不再带上，避免整段解说失败。
_terminology_dropped = False


def reset_terminology_fallback() -> None:
    global _terminology_dropped
    _terminology_dropped = False


def translate_terminology_names() -> list[str]:
    name = os.environ.get("TRANSLATE_TERMINOLOGY", "").strip()
    if not name or _terminology_dropped:
        return []
    return [name]


def _terminology_error(exc: BaseException) -> bool:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
    blob = f"{code}\n{exc}".lower()
    if code in {
        "ResourceNotFoundException",
        "AccessDeniedException",
        "UnauthorizedException",
        "AccessDenied",
        "UnauthorizedOperation",
    }:
        return True
    if "resourcenotfoundexception" in blob or "accessdeniedexception" in blob:
        return True
    if "terminolog" not in blob:
        return False
    return any(hint in blob for hint in (
        "not found", "does not exist", "doesn't exist", "no such",
        "not authorized", "unauthorized", "access denied", "permission",
        "forbidden", "invalid",
    ))


def translate_with_optional_terminology(call):
    """call(names) 收到术语表名列表；不使用术语表时收到 None。"""
    global _terminology_dropped
    names = translate_terminology_names()
    if not names:
        return call(None)
    try:
        return call(names)
    except (Exception, SystemExit) as exc:
        if not _terminology_error(exc):
            raise
        _terminology_dropped = True
        return call(None)


def boto_translate_chunk(translate, chunk: str) -> str:
    def call(names: list[str] | None) -> str:
        kwargs = {
            "Text": chunk,
            "SourceLanguageCode": "en",
            "TargetLanguageCode": "zh",
        }
        if names:
            kwargs["TerminologyNames"] = list(names)
        return translate.translate_text(**kwargs)["TranslatedText"]

    return translate_with_optional_terminology(call)


def cli_translate_chunk(chunk: str) -> str:
    def call(names: list[str] | None) -> str:
        args = [
            "translate", "translate-text",
            "--source-language-code", "en",
            "--target-language-code", "zh",
            "--text", chunk,
        ]
        if names:
            args.extend(["--terminology-names", *names])
        result = json.loads(_aws(args))
        return result["TranslatedText"]

    return translate_with_optional_terminology(call)


def _translate(text: str) -> str:
    return _translate_text(text, cli_translate_chunk)


def transcribe_job_name(clip: str, now: int) -> str:
    """Transcribe 的作业名只允许字母、数字和 ._-。"""
    safe = re.sub(r"[^0-9A-Za-z._-]", "-", clip)[:80].strip("-") or "clip"
    return f"deepcourt-{safe}-{now}"[:200]


def _translate_text(text: str, translate_one) -> str:
    pieces = []
    rest = text.strip()
    while rest:
        chunk = rest[:4500]
        if len(rest) > 4500:
            cut = chunk.rfind(" ")
            if cut > 1000:
                chunk = chunk[:cut]
        pieces.append(polish_zh(translate_one(chunk)))
        rest = rest[len(chunk):].strip()
    return "".join(pieces)


def _speak(text: str, dest: Path) -> None:
    # Neural 单次上限 3000 个字符。一条 20 秒左右的解说远短于这个上限。
    if len(text) > 3000:
        raise SystemExit("中文解说超过 Polly Neural 单次 3000 字")
    script = dest.with_suffix(".txt")
    script.write_text(text, encoding="utf-8")
    _aws([
        "polly", "synthesize-speech",
        "--engine", ENGINE,
        "--language-code", "cmn-CN",
        "--voice-id", VOICE_ID,
        "--output-format", "mp3",
        "--text", f"file://{script}",
        str(dest),
    ], json_out=False)


def _mix_zh(lines: list[tuple[Path, float, float]], duration_s: float, dest: Path) -> None:
    """每句按原解说的开始时间铺上。用 itsoffset，不用 adelay，避免时间戳溢出把音轨弄没。"""
    if not lines:
        raise SystemExit("没有可合成的中文句子")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{duration_s:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    filters = ["[0:a]aformat=channel_layouts=stereo[a0]"]
    labels = ["[a0]"]
    for index, (speech, start_s, slot_s) in enumerate(lines, start=1):
        rate = playback_rate(media_duration(speech), slot_s)
        tempo = f"atempo={rate:.4f}," if rate > 1.01 else ""
        cmd.extend(["-itsoffset", f"{max(0.0, start_s):.3f}", "-i", str(speech)])
        filters.append(f"[{index}:a]aformat=channel_layouts=stereo,{tempo}atrim=duration={slot_s:.3f}[a{index}]")
        labels.append(f"[a{index}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[out]"
    )
    cmd.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        str(dest),
    ])
    _run(cmd)
    _assert_playable(dest, duration_s)


def _write_en(source: Path, duration_s: float, dest: Path) -> None:
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-af", f"apad=whole_dur={duration_s:.3f},atrim=end={duration_s:.3f}",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        str(dest),
    ])


def _remux(picture: Path, audio: Path, dest: Path) -> None:
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(picture), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dest),
    ])
    _assert_playable(dest, media_duration(picture))


def _save_clip(clip: str, meta: dict) -> None:
    dest = WEB / "replay" / "clips.js"
    prefix = "window.DEEPCOURT_CLIPS="
    text = dest.read_text(encoding="utf-8")
    data = json.loads(text[len(prefix):].strip().rstrip(";"))
    if clip not in data:
        raise SystemExit(f"clips.js 里没有 {clip}")
    data[clip].update(meta)
    dest.write_text(prefix + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def dub_mp4s(
    source: Path,
    picture: Path,
    work: Path,
    *,
    clip: str,
    media_bucket: str,
    s3,
    transcribe,
    translate,
    polly,
) -> dict:
    """给云上成片做两条音轨。调用方用任务角色的 boto3，不依赖本机 profile。"""
    if not source.is_file():
        raise SystemExit(f"找不到源片 {source}")
    if not picture.is_file():
        raise SystemExit(f"找不到成片 {picture}")
    work.mkdir(parents=True, exist_ok=True)
    duration = media_duration(picture)
    wav = work / "asr.wav"
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-t", f"{duration:.3f}", str(wav),
    ])
    print(f"{clip} 上传并转写英文解说")
    transcript = _transcribe_boto(clip, wav, media_bucket, s3, transcribe, work)
    meta = _compose(clip, source, picture, work, duration, transcript, lambda text, dest: _speak_boto(text, dest, polly, translate))
    print(f"{clip} 英文：{meta['scriptEn']}")
    print(f"{clip} 中文：{meta['scriptZh']}")
    return meta


def _transcribe_boto(clip: str, wav: Path, bucket: str, s3, transcribe, work: Path) -> dict:
    key = f"commentary/asr/{clip}.wav"
    s3.upload_file(str(wav), bucket, key, ExtraArgs={"ContentType": "audio/wav"})
    job = transcribe_job_name(clip, int(time.time()))
    out_key = f"commentary/asr/{job}.json"
    transcribe.start_transcription_job(
        TranscriptionJobName=job,
        LanguageCode="en-US",
        MediaFormat="wav",
        Media={"MediaFileUri": f"s3://{bucket}/{key}"},
        OutputBucketName=bucket,
        OutputKey=out_key,
    )
    deadline = time.time() + 180
    status = "IN_PROGRESS"
    reason = ""
    while time.time() < deadline:
        got = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]
        status = got["TranscriptionJobStatus"]
        reason = got.get("FailureReason") or ""
        if status in ("COMPLETED", "FAILED"):
            break
        time.sleep(3)
    if status != "COMPLETED":
        raise SystemExit(f"{clip} 转写没有完成：{status} {reason}")
    body = s3.get_object(Bucket=bucket, Key=out_key)["Body"].read()
    (work / "transcript.json").write_bytes(body)
    return json.loads(body)


def _speak_boto(text: str, dest: Path, polly, translate) -> None:
    chinese = prefer_broadcast(text, "") or _translate_text(
        text, lambda chunk: boto_translate_chunk(translate, chunk),
    )
    if len(chinese) > 3000:
        raise SystemExit("中文解说超过 Polly Neural 单次 3000 字")
    audio = polly.synthesize_speech(
        Engine=ENGINE,
        LanguageCode="cmn-CN",
        VoiceId=VOICE_ID,
        OutputFormat="mp3",
        Text=chinese,
        TextType="text",
    )
    dest.write_bytes(audio["AudioStream"].read())
    dest.with_suffix(".txt").write_text(chinese, encoding="utf-8")


def _compose(clip: str, source: Path, picture: Path, work: Path, duration: float, transcript: dict, speak) -> dict:
    results = transcript.get("results") or {}
    raw = " ".join((row.get("transcript") or "").strip() for row in results.get("transcripts") or []).strip()
    if not raw:
        raise SystemExit(f"{clip} 没有转写出英文解说")
    sentences = prepare_sentences(results.get("items") or [])
    if not sentences:
        raise SystemExit(f"{clip} 没有可对齐的英文句子")
    spoken = []
    lines = []
    for index, sentence in enumerate(sentences):
        speech = work / f"zh-{index}.mp3"
        speak(sentence["text"], speech)
        chinese = speech.with_suffix(".txt").read_text(encoding="utf-8").strip()
        if not chinese:
            continue
        spoken.append(chinese)
        nxt = sentences[index + 1]["start"] if index + 1 < len(sentences) else duration
        slot = max(0.4, float(nxt) - float(sentence["start"]) - 0.05)
        lines.append((speech, float(sentence["start"]), slot))
    script_zh = "".join(spoken)
    if not script_zh:
        raise SystemExit(f"{clip} 没有译出中文")
    en_m4a = work / "en.m4a"
    zh_m4a = work / "zh.m4a"
    _write_en(source, duration, en_m4a)
    _mix_zh(lines, duration, zh_m4a)
    en_mp4 = work / "en.mp4"
    zh_mp4 = work / "zh.mp4"
    _remux(picture, en_m4a, en_mp4)
    _remux(picture, zh_m4a, zh_mp4)
    return {
        "scriptEn": " ".join(sentence["text"] for sentence in sentences),
        "scriptZh": script_zh,
        "tracks": {"zh": f"assets/{clip}-zh.mp4", "en": f"assets/{clip}-en.mp4"},
        "voice": VOICE_NOTE,
        "files": {"en": en_mp4, "zh": zh_mp4},
    }


def build_clip_tracks(clip: str, *, reuse_asr: bool = False) -> dict:
    source = _source_video(clip)
    picture = WEB / "assets" / f"{clip}-broadcast.mp4"
    if not source.exists():
        raise SystemExit(f"找不到源片 {source}")
    if not picture.exists():
        raise SystemExit(f"找不到成片 {picture}")
    if not shutil_which("ffmpeg") or not shutil_which("ffprobe"):
        raise SystemExit("需要本机 ffmpeg 和 ffprobe")
    WORK.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)
    duration = media_duration(picture)
    wav = WORK / f"{clip}-asr.wav"
    saved = WORK / f"{clip}.transcript.json"
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-t", f"{duration:.3f}", str(wav),
    ])
    if reuse_asr and saved.exists():
        print(f"{clip} 使用已经转写的结果")
        transcript = json.loads(saved.read_text(encoding="utf-8"))
    else:
        print(f"{clip} 上传并转写英文解说")
        transcript = _transcribe(clip, wav, _media_bucket())
        saved.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    results = transcript.get("results") or {}
    script_en = " ".join((row.get("transcript") or "").strip() for row in results.get("transcripts") or []).strip()
    if not script_en:
        raise SystemExit(f"{clip} 没有转写出英文解说")
    sentences = prepare_sentences(results.get("items") or [])
    if not sentences:
        raise SystemExit(f"{clip} 没有可对齐的英文句子")
    script_en = " ".join(sentence["text"] for sentence in sentences)
    print(f"{clip} 翻译并合成中文")
    spoken = []
    lines = []
    for index, sentence in enumerate(sentences):
        chinese = prefer_broadcast(sentence["text"], "") or _translate(sentence["text"])
        if not chinese:
            continue
        spoken.append(chinese)
        speech = WORK / f"{clip}-zh-{index}.mp3"
        _speak(chinese, speech)
        nxt = sentences[index + 1]["start"] if index + 1 < len(sentences) else duration
        slot = max(0.4, float(nxt) - float(sentence["start"]) - 0.05)
        lines.append((speech, float(sentence["start"]), slot))
    script_zh = "".join(spoken)
    if not script_zh:
        raise SystemExit(f"{clip} 没有译出中文")
    assets = WEB / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    en_m4a = assets / f"{clip}-en.m4a"
    zh_m4a = assets / f"{clip}-zh.m4a"
    _write_en(source, duration, en_m4a)
    start = float(sentences[0]["start"])
    _mix_zh(lines, duration, zh_m4a)
    en_mp4 = assets / f"{clip}-en.mp4"
    zh_mp4 = assets / f"{clip}-zh.mp4"
    _remux(picture, en_m4a, en_mp4)
    _remux(picture, zh_m4a, zh_mp4)
    meta = {
        "scriptEn": script_en,
        "scriptZh": script_zh,
        "tracks": {"zh": f"assets/{clip}-zh.mp4", "en": f"assets/{clip}-en.mp4"},
        "voice": VOICE_NOTE,
        "voiceId": VOICE_ID,
        "engine": ENGINE,
        "speechStart": start,
    }
    (AUDIO / f"{clip}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public = {
        "scriptEn": script_en,
        "scriptZh": script_zh,
        "tracks": meta["tracks"],
        "voice": VOICE_NOTE,
    }
    _save_clip(clip, public)
    print(f"{clip} 英文：{script_en}")
    print(f"{clip} 中文：{script_zh}")
    print(en_mp4)
    print(zh_mp4)
    return meta


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def main() -> None:
    parser = argparse.ArgumentParser(description="为成片做出英文原声和中文解说两条音轨")
    parser.add_argument("--clip", action="append", required=True)
    parser.add_argument("--reuse-asr", action="store_true")
    args = parser.parse_args()
    for clip in args.clip:
        build_clip_tracks(clip, reuse_asr=args.reuse_asr)


if __name__ == "__main__":
    main()
