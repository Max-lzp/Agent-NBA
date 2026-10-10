#!/bin/sh
# 只更新回放页的成片和数据。不跑 CDK，也不重传整个网站。
# 用法：scripts/publish_homepage.sh <成片.mp4> <clips.js>
set -eu
if [ "$#" -ne 2 ]; then
  echo "用法：scripts/publish_homepage.sh <成片.mp4> <clips.js>" >&2
  exit 1
fi
FILM=$1
CLIPS=$2
PROFILE=nba
REGION=us-east-1
cd "$(dirname "$0")/.."
BUCKET=$(aws cloudformation describe-stacks --stack-name Team04DeepCourt --profile "$PROFILE" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='WebBucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name Team04DeepCourt --profile "$PROFILE" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
NAME=$(basename "$FILM")
aws s3 cp "$FILM" "s3://$BUCKET/assets/$NAME" --content-type video/mp4 --profile "$PROFILE" --region "$REGION"
aws s3 cp "$CLIPS" "s3://$BUCKET/replay/clips.js" --content-type "application/javascript; charset=utf-8" --profile "$PROFILE" --region "$REGION"
aws s3 cp web/game.html "s3://$BUCKET/game.html" --content-type "text/html; charset=utf-8" --profile "$PROFILE" --region "$REGION"
aws s3 cp web/game.js "s3://$BUCKET/game.js" --content-type "application/javascript; charset=utf-8" --profile "$PROFILE" --region "$REGION"
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/assets/$NAME" "/replay/clips.js" "/game.html" "/game.js" --profile "$PROFILE" >/dev/null
echo "已更新 /assets/$NAME 和回放数据"
