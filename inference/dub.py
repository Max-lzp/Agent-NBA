"""云上成片的英文原声和中文解说。

任务容器里没有仓库包。启动时会把 commentary.py 下到 /opt/track，这里优先加载它。
本机直接跑时退回 pipeline.present.tracks。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def attach(source: Path, picture: Path, work: Path, media_bucket: str, clip: str) -> dict:
    import boto3

    mod = _tracks()
    region = os.environ.get("AWS_REGION", "us-east-1")
    session = boto3.session.Session()
    return mod.dub_mp4s(
        source,
        picture,
        work,
        clip=clip,
        media_bucket=media_bucket,
        s3=session.client("s3", region_name=region),
        transcribe=session.client("transcribe", region_name=region),
        translate=session.client("translate", region_name=region),
        polly=session.client("polly", region_name=region),
    )


def attach_script(source: Path, picture: Path, work: Path, clip: str, lines: list[dict]) -> dict:
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    polly = boto3.session.Session().client("polly", region_name=region)
    return _tracks().dub_script(source, picture, work, clip=clip, lines=lines, polly=polly)


def _tracks():
    lib = Path(__file__).resolve().with_name("commentary.py")
    if lib.is_file():
        spec = importlib.util.spec_from_file_location("commentary", lib)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 {lib}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from pipeline.present import tracks
    return tracks
