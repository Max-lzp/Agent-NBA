"""快手扫码绑定。密钥只在服务端读，前端只拿到授权地址。"""

from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import urllib.request

import common
from kuaishou.urls import build_connect_url
from runtime_secrets import load_secret, store_secret

SECRET_ARN = os.environ["KUAISHOU_SECRET_ARN"]
CALLBACK_URL = os.environ["CALLBACK_URL"]
REGION = os.environ.get("AWS_REGION", "us-east-1")


def handler(event, _context):
    path = str(event.get("path") or event.get("rawPath") or "")
    if path.endswith("/callback"):
        return _callback(event)
    return _start()


def _start():
    secret = load_secret(SECRET_ARN, REGION)
    app_id = str(secret.get("app_id") or "").strip()
    if not app_id:
        return common.json_response(200, {
            "url": "",
            "message": "敬请期待。快手资质还在审核，通过后写入 app_id。",
        })
    state = secrets.token_urlsafe(18)
    secret["pending_state"] = state
    store_secret(SECRET_ARN, REGION, secret)
    return common.json_response(200, {
        "url": build_connect_url(app_id, CALLBACK_URL, state),
        "message": "用快手 APP 扫码绑定。",
    })


def _callback(event):
    params = event.get("queryStringParameters") or {}
    code = str(params.get("code") or "")
    state = str(params.get("state") or "")
    secret = load_secret(SECRET_ARN, REGION)
    if not code or not state or state != str(secret.get("pending_state") or ""):
        return _html(400, "授权没有完成。请回到播放器再点一次快手。")
    app_id = str(secret.get("app_id") or "")
    app_secret = str(secret.get("app_secret") or "")
    if not app_id or not app_secret:
        return _html(503, "应用密钥还没写入。")
    token = _exchange(app_id, app_secret, code)
    if not token.get("access_token"):
        return _html(502, "快手没有换到访问凭证。")
    secret["access_token"] = token.get("access_token")
    secret["refresh_token"] = token.get("refresh_token") or secret.get("refresh_token") or ""
    secret["open_id"] = token.get("open_id") or ""
    secret["pending_state"] = ""
    store_secret(SECRET_ARN, REGION, secret)
    return _html(200, "快手账号已绑定。可以关掉这个窗口。")


def _exchange(app_id: str, app_secret: str, code: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "app_id": app_id,
            "app_secret": app_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
    )
    url = f"https://open.kuaishou.com/oauth2/access_token?{query}"
    with urllib.request.urlopen(url, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _html(status: int, text: str) -> dict:
    body = f"<!DOCTYPE html><html lang=zh-CN><meta charset=utf-8><title>快手</title><p>{text}</p>"
    return {
        "statusCode": status,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": body,
    }
