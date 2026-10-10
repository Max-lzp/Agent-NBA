#!/usr/bin/env python3
"""本地发布台。只听 127.0.0.1，给 /hub.html 提供 GitHub 状态和 CloudFront 部署。

不接受任意命令。推送只执行 git push，部署只执行与 scripts/publish.sh 相同的 cdk deploy。
"""
from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8791
PROFILE = "nba"
REGION = "us-east-1"
DOMAIN = "d2vzd2batw1eqe.cloudfront.net"
STACK = "Team04DeepCourt"
GITHUB = "https://github.com/jasonlovegugu/nba-hackathon-team04"

_lock = threading.Lock()
_job = {
    "state": "idle",
    "kind": "",
    "log": "",
    "code": None,
}


def _run(args: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        done = subprocess.run(
            args,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "命令超时"
    text = (done.stdout or "") + (done.stderr or "")
    return done.returncode, text.rstrip()


def _git_status() -> dict:
    code, name = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=8)
    branch_name = name if code == 0 else "unknown"
    code, head = _run(["git", "log", "-1", "--format=%h %s"], timeout=8)
    short, _, subject = (head.partition(" ") if code == 0 else ("", "", ""))
    code, up = _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=8)
    upstream = up if code == 0 else ""
    ahead = behind = None
    remote_has = False
    if upstream:
        remote_has = True
        code, counts = _run(["git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD"], timeout=8)
        if code == 0 and counts:
            left, _, right = counts.partition("\t")
            if not right:
                left, _, right = counts.partition(" ")
            try:
                behind, ahead = int(left), int(right)
            except ValueError:
                ahead = behind = None
    else:
        code, _ = _run(["git", "rev-parse", "--verify", f"origin/{branch_name}"], timeout=8)
        remote_has = code == 0
        if remote_has:
            code, counts = _run(
                ["git", "rev-list", "--left-right", "--count", f"origin/{branch_name}...HEAD"],
                timeout=8,
            )
            if code == 0 and counts:
                left, _, right = counts.partition("\t")
                if not right:
                    left, _, right = counts.partition(" ")
                try:
                    behind, ahead = int(left), int(right)
                except ValueError:
                    ahead = behind = None
    code, porcelain = _run(["git", "status", "--porcelain"], timeout=15)
    lines = [line for line in porcelain.splitlines() if line.strip()] if code == 0 else []
    modified = sum(1 for line in lines if not line.startswith("??"))
    untracked = sum(1 for line in lines if line.startswith("??"))
    sample = [line[3:] if len(line) > 3 else line for line in lines[:16]]
    return {
        "repo": GITHUB,
        "branch": branch_name,
        "upstream": upstream,
        "remoteHasBranch": remote_has,
        "ahead": ahead,
        "behind": behind,
        "head": short,
        "subject": subject,
        "modified": modified,
        "untracked": untracked,
        "sample": sample,
    }


def _cloudfront_status() -> dict:
    out = {
        "url": f"https://{DOMAIN}/",
        "domain": DOMAIN,
        "distributionId": "",
        "distributionStatus": "",
        "stack": STACK,
        "stackStatus": "",
        "account": "",
        "profile": PROFILE,
        "region": REGION,
        "error": "",
    }
    code, text = _run(
        ["aws", "sts", "get-caller-identity", "--profile", PROFILE, "--region", REGION, "--query", "Account", "--output", "text"],
        timeout=12,
    )
    if code != 0:
        out["error"] = text or "profile nba 还不能用"
        return out
    out["account"] = text.strip()
    code, text = _run(
        [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", STACK,
            "--profile", PROFILE,
            "--region", REGION,
            "--query", "Stacks[0].StackStatus",
            "--output", "text",
        ],
        timeout=15,
    )
    if code == 0:
        out["stackStatus"] = text.strip()
    else:
        out["error"] = text or "读不到栈状态"
    code, text = _run(
        [
            "aws", "cloudfront", "list-distributions",
            "--profile", PROFILE,
            "--query",
            f"DistributionList.Items[?DomainName=='{DOMAIN}'].[Id,Status] | [0]",
            "--output", "text",
        ],
        timeout=20,
    )
    if code == 0 and text and text != "None":
        parts = text.split()
        if parts:
            out["distributionId"] = parts[0]
        if len(parts) > 1:
            out["distributionStatus"] = parts[1]
    elif not out["error"]:
        out["error"] = text or "读不到 CloudFront 分配"
    return out


def _start_job(kind: str, args: list[str], cwd: Path = REPO, env_extra: dict | None = None) -> bool:
    with _lock:
        if _job["state"] == "running":
            return False
        _job["state"] = "running"
        _job["kind"] = kind
        _job["log"] = ""
        _job["code"] = None

    def run() -> None:
        import os
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        assert proc.stdout is not None
        chunks: list[str] = []
        for line in proc.stdout:
            chunks.append(line)
            with _lock:
                _job["log"] = "".join(chunks)[-20000:]
        code = proc.wait()
        with _lock:
            _job["code"] = code
            _job["state"] = "ok" if code == 0 else "failed"
            if not _job["log"]:
                _job["log"] = f"退出码 {code}"

    threading.Thread(target=run, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/status":
            with _lock:
                job = dict(_job)
            with ThreadPoolExecutor(max_workers=2) as pool:
                git_f = pool.submit(_git_status)
                cf_f = pool.submit(_cloudfront_status)
                payload = {"github": git_f.result(), "cloudfront": cf_f.result(), "job": job}
            self._send(200, payload)
            return
        if path == "/job":
            with _lock:
                job = dict(_job)
            self._send(200, job)
            return
        self._send(404, {"error": "没有这个路径"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/github/push":
            ok = _start_job("github", ["git", "push", "-u", "origin", "HEAD"])
            self._send(200 if ok else 409, {"ok": ok, "error": "" if ok else "已有任务在跑"})
            return
        if path == "/cloudfront/deploy":
            code, account = _run(
                ["aws", "sts", "get-caller-identity", "--profile", PROFILE, "--region", REGION, "--query", "Account", "--output", "text"],
                timeout=12,
            )
            if code != 0:
                self._send(400, {"ok": False, "error": account or "profile nba 还不能用"})
                return
            ok = _start_job(
                "cloudfront",
                [
                    "npx", "cdk", "deploy", STACK,
                    "-c", "enableP1=false",
                    "-c", "enableP2=false",
                    "--profile", PROFILE,
                    "--require-approval", "never",
                ],
                cwd=REPO / "cdk",
                env_extra={
                    "CDK_DEFAULT_ACCOUNT": account.strip(),
                    "CDK_DEFAULT_REGION": REGION,
                },
            )
            self._send(200 if ok else 409, {"ok": ok, "error": "" if ok else "已有任务在跑"})
            return
        self._send(404, {"error": "没有这个路径"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[ops] {self.address_string()} {fmt % args}")


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"发布台控制服务 http://{HOST}:{PORT}  仓库 {REPO}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
