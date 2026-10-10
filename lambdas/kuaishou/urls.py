"""快手扫码地址。app_secret 不出现在这个字符串里。"""

from __future__ import annotations

from urllib.parse import urlencode


def build_connect_url(app_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "app_id": app_id,
            "scope": "user_info,user_video_publish",
            "response_type": "code",
            "ua": "pc",
            "redirect_uri": redirect_uri,
            "state": state,
            "size": "280",
        }
    )
    return f"https://open.kuaishou.com/oauth2/connect?{query}"
