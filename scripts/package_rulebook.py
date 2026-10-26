"""Validate and package provenance for public NBA Video Rulebook examples.

Does not copy videos to the public site. --probe performs ordinary verified-TLS
requests and retains exact media bytes only in a local test cache.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def records():
    data = json.loads((ROOT / "data/official-rulebook/clips.json").read_text())
    assert data["schemaVersion"] == 1 and len(data["clips"]) == 2
    for clip in data["clips"]:
        assert re.fullmatch(r"[a-z-]+", clip["id"])
        for field, host in [("officialPage", "videorulebook.nba.com"), ("videoUrl", "ak-static.cms.nba.com")]:
            url = urllib.parse.urlsplit(clip[field])
            assert url.scheme == "https" and url.hostname == host and not url.username
        assert clip["duration"] > 0 and len(clip["mediaSha256"]) == 64
        assert set(clip["notes"]) == {"expert", "new", "together"}
    return data


def check(clip, cache):
    cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(clip["officialPage"], timeout=25) as response:
        page = response.read(2 * 1024 * 1024).decode()
    links = [html.unescape(url) for url in re.findall(r'<source\s+src="([^"]+)"', page)]
    if clip["videoUrl"] not in links:
        raise ValueError("Official page no longer lists the recorded media")
    with urllib.request.urlopen(clip["videoUrl"], timeout=30) as response:
        media = response.read(64 * 1024 * 1024 + 1)
        if response.status != 200 or len(media) > 64 * 1024 * 1024:
            raise ValueError("Unexpected media status or size")
    if hashlib.sha256(media).hexdigest() != clip["mediaSha256"]:
        raise ValueError("Official media changed; review it before updating the snapshot")
    file = cache / (clip["id"] + ".mp4")
    file.write_bytes(media)
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(file)], text=True))
    if abs(float(probe["format"]["duration"]) - clip["duration"]) > .1 or not any(s["codec_type"] == "video" for s in probe["streams"]):
        raise ValueError("Media validation mismatch")
    return {"id": clip["id"], "checkedAt": datetime.now(timezone.utc).isoformat(), "status": "source_and_media_verified", "duration": float(probe["format"]["duration"]), "mediaSha256": clip["mediaSha256"], "bytes": len(media)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/agent-nba-rulebook-cache"))
    args = parser.parse_args()
    data = records()
    if args.probe:
        report = []
        for clip in data["clips"]:
            try:
                result = check(clip, args.cache)
            except Exception as error:
                result = {"id": clip["id"], "status": "not_verified", "error": str(error)[:250]}
            print(clip["id"], result["status"])
            report.append(result)
        (ROOT / "web/replay/rulebook-validation.json").write_text(json.dumps({"results": report}, ensure_ascii=False, indent=2) + "\n")
        if any(row["status"] != "source_and_media_verified" for row in report):
            raise SystemExit(1)
    (ROOT / "web/replay/official-rulebook.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print("Packaged two official rule examples; no video files added to the site")


if __name__ == "__main__":
    main()
