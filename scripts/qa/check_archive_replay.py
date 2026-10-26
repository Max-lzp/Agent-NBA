"""Browser regression of the actual restored competition page, no cloud.

python3 scripts/qa/check_archive_replay.py
Requires Playwright and Chromium. Builds its own disposable site directory.
"""
import functools
import importlib.util
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
    spec = importlib.util.spec_from_file_location("archive_builder", ROOT / "scripts/build_archive_site.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory() as directory:
        site = builder.build(Path(directory) / "site")
        pack = builder.archive_pack()
        assert pack["preRenderedFreeze"] and not pack.get("tracks")
        assert len(pack["events"]) == 11
        # All original events predate the inserted freeze: their alignment is
        # retained rather than stretched over the rendered file's longer time.
        for event in pack["events"]:
            if "originalTime" in event:
                assert event["t"] == event["originalTime"]
        assert 20.98 < pack["archiveFreeze"]["start"] < 21
        assert pack["duration"] > pack["archiveFreeze"]["end"]
        handler = functools.partial(QuietHandler, directory=str(site))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                executable = shutil.which("chromium")
                browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors, failed, requests = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.on("response", lambda response: failed.append((response.status, response.url)) if response.status >= 400 else None)
                assert page.goto(base + "/").status == 200
                expect(page.locator("#mode-modal")).to_be_visible()
                page.locator('[data-mode="xinfan"]').click()
                expect(page.locator("#mode-modal")).to_be_hidden()
                page.wait_for_function('document.querySelector("video").readyState >= 2')
                assert abs(page.locator("video").evaluate("v => v.duration") - pack["duration"]) < .1
                assert page.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--orange").trim()') == "#f1681e"
                assert page.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()') == "#14110e"
                page.locator("video").evaluate("v => v.play()")
                page.wait_for_function('document.querySelector("video").currentTime > .1')
                for mode in ["laofan", "xinfan", "daikan"]:
                    page.locator(f'[data-watch="{mode}"]').click()
                    expect(page.locator(f'[data-watch="{mode}"]')).to_have_attribute("aria-pressed", "true")
                    if mode == "laofan": expect(page.locator("#glossary")).to_be_hidden()
                    else: expect(page.locator("#glossary")).to_be_visible()
                expect(page.locator("#floor img[alt$='头部特写']")).not_to_have_count(0)
                expect(page.locator("#floor")).to_contain_text("Cooper Flagg")
                page.locator("#events button").filter(has_text="弗拉格 2 分命中").click()
                assert abs(page.locator("video").evaluate("v => v.currentTime") - 17.02) < .1
                expect(page.locator("#detail")).to_contain_text("50.4%")
                assert page.locator("video").evaluate("v => v.paused")
                page.locator("#events button").filter(has_text="出手定格回看").click()
                assert abs(page.locator("video").evaluate("v => v.currentTime") - 21.037) < .1
                expect(page.locator("#court svg")).to_be_visible()
                assert page.locator("#freeze").count() == 0  # no duplicate JS freeze
                page.locator(".archive-questions button").first.click()
                expect(page.locator(".archive-answer")).to_contain_text("50.4%")
                expect(page.locator(".archive-source")).to_contain_text("快照")
                for width in [1440, 390]:
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                # Legacy job IDs and absent clips must not poll AWS or break playback.
                page.goto(base + "/game.html?jobId=exam&clip=amen&mode=xinfan")
                page.wait_for_function('document.querySelector("video").readyState >= 2')
                expect(page.locator("#subhead")).to_contain_text("弗拉格")
                page.goto(base + "/readme.html")
                expect(page.locator("body")).to_contain_text("作品说明归档")
                page.get_by_role("link", name="返回当前离线回放").click()
                page.wait_for_function('document.querySelector("video").readyState >= 2')
                assert not errors, errors
                assert not failed, failed
                assert all(url.startswith((base, "blob:")) for url in requests), requests
                assert not any("/api/" in url for url in requests)
                browser.close()
        finally:
            server.shutdown()
    print("PASS: original theme, real archived video, 11 events, seek/pause, 3 modes, rosters/headshots, data cards, heatmap, no duplicate freeze, archived answers, legacy URLs, docs, desktop/mobile, no missing assets or AWS calls")


if __name__ == "__main__":
    main()
