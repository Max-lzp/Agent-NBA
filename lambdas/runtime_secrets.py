"""运行时读 Secrets Manager。密钥不进仓库。"""

from __future__ import annotations

import json

import boto3


def load_secret(arn: str, region: str) -> dict:
    if not arn:
        return {}
    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=arn)
    text = raw.get("SecretString") or ""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def store_secret(arn: str, region: str, data: dict) -> None:
    boto3.client("secretsmanager", region_name=region).put_secret_value(
        SecretId=arn,
        SecretString=json.dumps(data, ensure_ascii=False),
    )
