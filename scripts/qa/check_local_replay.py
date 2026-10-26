"""Browser regression check: python scripts/qa/check_local_replay.py.

Requires Playwright plus Chromium. Uses only local HTTP and local fixtures.
"""
import functools
import json
from pathlib import Path
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
            executable = shutil.which("chromium")
            browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page(accept_downloads=True)
            errors, failed, requests = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.on("response", lambda response: failed.append(response.url) if response.status >= 400 else None)
            assert page.goto(base + "/portfolio/player.html").status == 200
            expect(page.locator("#export")).to_be_enabled()
            assert abs(page.locator("video").evaluate("v => v.duration") - 12) < 0.1
            page.locator("video").evaluate("v => v.play()")
            page.wait_for_function("document.querySelector('video').currentTime > 0.1")
            page.locator("#timeline button").nth(2).click()
            expect(page.locator("#event-heading")).to_have_text("三分出手")
            assert abs(page.locator("video").evaluate("v => v.currentTime") - 6) < 0.2
            assert page.locator("video").evaluate("v => v.paused")
            descriptions = []
            for mode in ["expert", "new", "together"]:
                page.locator(f'[data-mode="{mode}"]').click()
                expect(page.locator(f'[data-mode="{mode}"]')).to_have_attribute("aria-pressed", "true")
                descriptions.append(page.locator("#event-description").inner_text())
            assert len(set(descriptions)) == 3
            page.locator("#query").fill("三分")
            page.locator("#lookup-form button").click()
            expect(page.locator("#lookup-result")).to_contain_text("06")
            page.locator("#query").fill("不存在的球员")
            page.locator("#lookup-form button").click()
            expect(page.locator("#lookup-result")).to_contain_text("没有匹配内容")

            # Untrusted text must remain text after annotation/export/import.
            payload = '<img src=x onerror="window.injected=true">'
            page.locator("#event-title").fill(payload)
            page.locator("#event-note").fill("用户观察，不是模型输出")
            page.locator("#annotation-form button").click()
            expect(page.locator("#timeline button")).to_have_count(5)
            assert page.locator("#timeline img").count() == 0
            assert page.evaluate("window.injected !== true")
            with page.expect_download() as info:
                page.locator("#export").click()
            saved = Path(directory) / "timeline.json"
            info.value.save_as(saved)
            data = json.loads(saved.read_text())
            assert data["source"]["kind"] == "sample" and len(data["events"]) == 5
            page.locator("#remove-event").click()
            expect(page.locator("#timeline button")).to_have_count(4)
            page.locator("#timeline-file").set_input_files(saved)
            expect(page.locator("#timeline button")).to_have_count(5)
            assert page.locator("#timeline img").count() == 0
            invalid = Path(directory) / "invalid.json"
            data["events"][0]["t"] = 99
            invalid.write_text(json.dumps(data))
            page.locator("#timeline-file").set_input_files(invalid)
            expect(page.locator("#status")).to_contain_text("导入失败")
            expect(page.locator("#timeline button")).to_have_count(5)

            # Switching to a user file clears teaching facts; mismatched timelines fail atomically.
            page.locator("#video-file").set_input_files(ROOT / "web/assets/demo-possession.mp4")
            expect(page.locator("#export")).to_be_enabled()
            expect(page.locator("#source")).to_contain_text("demo-possession.mp4")
            expect(page.locator("#timeline button")).to_have_count(0)
            page.locator("#timeline-file").set_input_files(saved)
            expect(page.locator("#status")).to_contain_text("不匹配")
            expect(page.locator("#timeline button")).to_have_count(0)
            page.locator("#event-title").fill("本地观察")
            page.locator("#event-note").fill("人工复盘")
            page.locator("#annotation-form button").click()
            with page.expect_download() as info:
                page.locator("#export").click()
            local = Path(directory) / "local.json"
            info.value.save_as(local)
            assert json.loads(local.read_text())["source"]["kind"] == "local"
            page.locator("#remove-event").click()
            page.locator("#timeline-file").set_input_files(local)
            expect(page.locator("#timeline button")).to_have_count(1)
            for width in [1280, 390]:
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            assert not errors, errors
            assert not failed, failed
            assert all(url.startswith((base, "blob:")) for url in requests), requests
            assert not any("/api/" in url for url in requests)
            page.locator("#sample").click()
            expect(page.locator("#timeline button")).to_have_count(4)
            browser.close()
            print("PASS: playback, seeking, 3 modes, lookup, annotation, export/import, invalid/mismatched data rejection, inert HTML, local video, responsive layout, zero AWS/external requests")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
