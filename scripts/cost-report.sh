#!/bin/sh
# 按 project 标签看最近 7 天花费。账单里要先启用 project 这个成本分配标签。
set -eu
profile=${AWS_PROFILE:-team04}
end=$(date -u +%Y-%m-%d)
start=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%d)
aws ce get-cost-and-usage \
  --profile "$profile" \
  --region us-east-1 \
  --time-period "Start=${start},End=${end}" \
  --granularity DAILY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=project
