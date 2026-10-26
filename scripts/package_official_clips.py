"""Package NBA-official shot samples from integrity-checked source snapshots.

Default: no network. --probe tests the original CDN files with ordinary HTTPS,
without header spoofing or access-control workarounds. Videos are never bundled.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SELECTION = [("flagg_shot_sequence", 63), ("flagg_shot_sequence", 228), ("amen_shot_sequence", 18)]
NAMES = {1642843: "弗拉格", 1641708: "阿门·汤普森"}


def catalog():
    base = ROOT / "data/deep"
    manifest = json.loads((base / "manifest.json").read_text())
    clips = []
    for dataset, event in SELECTION:
        meta = manifest["datasets"][dataset]
        content = (base / meta["raw"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == meta["sha256"], "Official source snapshot hash mismatch"
        raw = json.loads(content)
        row = next(row for row in raw["shotList"] if row["eventNum"] == event)
        url = row["largeUrl"]
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "videos.nba.com"
        assert f'/{row["gameId"]}/{event}/' in parsed.path
        assert parsed.path.endswith(".mp4")
        value = {"2PT Field Goal": 2, "3PT Field Goal": 3}[row["shotType"]]
        q = row["shotQuality"]
        assert 0 <= q <= 1 and row["success"] in (0, 1)
        clips.append({
            "id": f'{row["gameId"]}-{event}', "gameId": row["gameId"], "eventId": event,
            "player": raw["playerName"], "playerZh": NAMES[row["playerId"]],
            "playerId": row["playerId"], "period": row["period"], "clock": row["gameClock"],
            "action": row["actionType"], "shotValue": value, "made": bool(row["success"]),
            "shotQuality": q, "expectedPoints": value * q, "actualPoints": value * row["success"],
            "videoUrl": url, "expectedDuration": row["largeDuration"] / 1000,
            "officialPage": f'https://www.nba.com/stats/events?GameID={row["gameId"]}&GameEventID={event}',
            "sourceUrl": meta["url"], "fetchedAt": meta["fetched_at"], "sourceSha256": meta["sha256"],
            "alignment": "official event ID only; no verified local frame/shot offset",
        })
    return {"schemaVersion": 1, "scope": "NBA official source snapshots; current media availability is separately tested", "clips": clips}


def probe(clip):
    record = {"id": clip["id"], "url": clip["videoUrl"], "checkedAt": datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(clip["videoUrl"], headers={"User-Agent": "Agent-NBA/1.0 official-source-check"})
        with urllib.request.urlopen(request, timeout=20) as response, tempfile.TemporaryDirectory() as directory:
            record.update(httpStatus=response.status, contentType=response.headers.get("Content-Type"), expectedDuration=clip["expectedDuration"])
            if urllib.parse.urlsplit(response.url).hostname != "videos.nba.com":
                raise ValueError("Unexpected media redirect; not followed for publication")
            data = response.read(24 * 1024 * 1024 + 1)
            record.update(bytes=len(data), mediaSha256=hashlib.sha256(data).hexdigest())
            if len(data) > 24 * 1024 * 1024:
                raise ValueError("Media exceeds sample limit; possible placeholder or wrong asset")
            if b"ftyp" not in data[:64]:
                raise ValueError("Response is not an MP4 sample")
            path = Path(directory) / "sample.mp4"
            path.write_bytes(data)
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=20)
            media = json.loads(result.stdout)
            duration = float(media["format"]["duration"])
            record["duration"] = duration
            if not any(row.get("codec_type") == "video" for row in media["streams"]):
                raise ValueError("No video stream")
            if abs(duration - clip["expectedDuration"]) > .6:
                raise ValueError("Duration differs from official snapshot; reject possible placeholder")
            record.update(status="media_probe_passed", bytes=len(data), duration=duration, mediaSha256=hashlib.sha256(data).hexdigest())
    except urllib.error.URLError as error:
        record.update(status="network_blocked" if "Tunnel connection failed: 403" in str(error) else "request_failed", error=str(error)[:300])
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        record.update(status="media_not_verified", error=str(error)[:300])
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    data = catalog()
    target = ROOT / "web/replay/official-clips.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f'Packaged {len(data["clips"])} official-source samples; snapshot hashes verified')
    if args.probe:
        records = [probe(clip) for clip in data["clips"]]
        report = {"schemaVersion": 1, "notes": "MP4/duration checks do not verify frame-level event alignment or usage rights", "results": records}
        (target.parent / "official-clips-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        for record in records:
            print(record["id"], record["status"])
        if any(row["status"] != "media_probe_passed" for row in records):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
