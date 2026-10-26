"""Validate the rulebook UI using exact real media previously fetched by --probe.

python scripts/package_rulebook.py --probe
python scripts/qa/check_rulebook.py --media-dir /tmp/agent-nba-rulebook-cache

The browser plays verified downloaded bytes locally. It does not establish
that every visitor's browser can reach the remote CDN.
"""
import argparse
import functools
import hashlib
import json
from pathlib import Path
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-dir", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads((ROOT / "web/replay/official-rulebook.json").read_text())
    media = {}
    for clip in data["clips"]:
        content = (args.media_dir / (clip["id"] + ".mp4")).read_bytes()
        assert hashlib.sha256(content).hexdigest() == clip["mediaSha256"]
        media[clip["videoUrl"]] = content
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            executable = shutil.which("chromium")
            browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page()
            errors, loaded = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Fulfil the exact verified real bytes, not the old synthetic fixture.
            def response(route):
                content = media[route.request.url]
                loaded.append(route.request.url)
                route.fulfill(status=200, content_type="video/mp4", body=content)
            page.route("https://ak-static.cms.nba.com/**", response)
            assert page.goto(base + "/rulebook.html").status == 200
            expect(page.locator("#rule-list button")).to_have_count(2)
            assert not loaded
            for i, clip in enumerate(data["clips"]):
                page.locator("#rule-list button").nth(i).click()
                expect(page.locator("#rule-status")).to_contain_text("官方片段已就绪")
                assert abs(page.locator("video").evaluate("v => v.duration") - clip["duration"]) < .1
                page.locator("video").evaluate("v => {v.muted=true; return v.play();}")
                page.wait_for_function('document.querySelector("video").currentTime > .1')
                page.locator("video").evaluate("v => v.pause()")
                descriptions = []
                for mode in ["expert", "new", "together"]:
                    page.locator(f'[data-mode="{mode}"]').click()
                    expect(page.locator(f'[data-mode="{mode}"]')).to_have_attribute("aria-pressed", "true")
                    descriptions.append(page.locator("#rule-detail").inner_text())
                assert len(set(descriptions)) == 3
                expect(page.locator("#rule-detail")).to_contain_text(clip["officialTitle"])
                assert page.locator("#rule-detail a").get_attribute("href") == clip["officialPage"]
            for width in [1280, 390]:
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path="/tmp/agent-nba-rulebook-mobile.png", full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
    print("PASS: two exact NBA media hashes, actual MP4 decoding/playback locally, source links, three explanations, no automatic external loading, desktop/mobile. Visitor-to-CDN connectivity is separate.")


if __name__ == "__main__":
    main()
