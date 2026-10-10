"""把模型输出收成两句中文。"""

from __future__ import annotations


def two_sentences(text: str) -> str:
    cleaned = " ".join((text or "").replace("\n", " ").split())
    if not cleaned:
        return ""
    parts = [part.strip() for part in cleaned.replace("！", "。").replace("？", "。").split("。") if part.strip()]
    chosen = parts[:2]
    if not chosen:
        return cleaned[:180]
    return "。".join(chosen) + "。"
