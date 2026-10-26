"""Test provenance and the official-source player with explicit local fixtures.

This does NOT establish live NBA video accessibility or semantic alignment.
"""
import functools
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    spec = importlib.util.spec_from_file_location("official_packager", ROOT / "scripts/package_official_clips.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.catalog()
    assert data == json.loads((ROOT / "web/replay/official-clips.json").read_text())
    assert len(data["clips"]) == 3
    assert [row["shotQuality"] for row in data["clips"]] == [.716, .382, .4]
    for row in data["clips"]:
        assert row["expectedPoints"] == row["shotValue"] * row["shotQuality"]
        assert row["actualPoints"] == row["shotValue"] * row["made"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
            fixture = Path(directory) / "fixture.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(ROOT / "portfolio/media/teaching.mp4"), "-t", "8.483", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(fixture)], check=True)
            executable = shutil.which("chromium")
            browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page()
            errors, cdn = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Interception is solely an offline fixture test, never a proxy to NBA.
            def media(route):
                cdn.append(route.request.url)
                route.fulfill(status=200, content_type="video/mp4", body=fixture.read_bytes())
            page.route("https://videos.nba.com/**", media)
            page.goto(base + "/official-clips.html")
            expect(page.locator("#official-list button")).to_have_count(3)
            assert not cdn  # no automatic connection to third-party media
            page.locator("#official-list button").first.click()
            expect(page.locator("#official-status")).to_contain_text("视频已解码")
            expect(page.locator("#official-detail")).to_contain_text("71.6%")
            expect(page.locator("#official-detail")).to_contain_text("63")
            assert page.locator("#official-detail a").count() == 3
            page.locator("video").evaluate("v => v.play()")
            page.wait_for_function('document.querySelector("video").currentTime > .1')
            page.unroute("https://videos.nba.com/**", media)
            def wrong(route):
                route.fulfill(status=200, content_type="video/mp4", body=(ROOT / "portfolio/media/teaching.mp4").read_bytes())
            page.route("https://videos.nba.com/**", wrong)
            page.locator("#official-list button").nth(1).click()
            expect(page.locator("#official-status")).to_contain_text("时长与官方记录不符")
            assert page.locator("video").get_attribute("src") is None
            page.unroute("https://videos.nba.com/**", wrong)
            page.route("https://videos.nba.com/**", lambda route: route.fulfill(status=403, body="fixture denied"))
            page.locator("#official-list button").nth(2).click()
            expect(page.locator("#official-status")).to_contain_text("源站暂时无法播放")
            for width in [1280, 390]:
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
    print("PASS: source snapshot hashes, event IDs, derived metrics; fixture-only browser playback, metadata display, wrong-duration rejection, CDN denial fallback, mobile layout. Live NBA media NOT verified.")


if __name__ == "__main__":
    main()
