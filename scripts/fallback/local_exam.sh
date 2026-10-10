#!/bin/sh
# 云上作业失败后，用仓库里的同一版代码在本机出考试片，上传到网页桶，只失效新文件。
# CloudFront 根路径要打开这支片子：回放数据会写上 DEEPCOURT_LEAD。
#
# 用法：scripts/fallback/local_exam.sh <视频路径> <片名>
#
# 环境变量（都可选）：
#   SCOREBOARD_ONLY=1  不烧人名，只烧记分牌
#   SKIP_VISION=1      这支片名的视觉结果已经在，不重跑追踪
#   SKIP_GAME_ID=1     不传 --game-id，让程序在已下载的场次里自己认
#   GAME_ID            默认 0022500701（弗拉格同场，独行侠客场对火箭）
#   AWAY HOME WHITE    默认 DAL、HOU、HOU
set -eu

if [ "$#" -ne 2 ]; then
  echo "用法：scripts/fallback/local_exam.sh <视频路径> <片名>" >&2
  exit 1
fi

CLIP=$2
case "$CLIP" in
  *[!A-Za-z0-9_-]*)
    echo "片名只能用字母、数字、下划线和中划线" >&2
    exit 1
    ;;
esac
if [ ! -f "$1" ]; then
  echo "找不到视频：$1" >&2
  exit 1
fi
# 后面会 cd 到仓库根目录，相对路径要先变成绝对路径。
VIDEO=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")

PROFILE=nba
REGION=us-east-1
STACK=Team04DeepCourt
AWAY=${AWAY:-DAL}
HOME=${HOME:-HOU}
WHITE=${WHITE:-HOU}
GAME_ID=${GAME_ID:-0022500701}
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

set -- python3 -m pipeline.run_clip --video "$VIDEO" --clip "$CLIP" --away "$AWAY" --home "$HOME" --white "$WHITE"
if [ "${SKIP_VISION:-0}" = "1" ]; then
  set -- "$@" --skip-vision
fi
if [ "${SKIP_GAME_ID:-0}" != "1" ]; then
  set -- "$@" --game-id "$GAME_ID"
fi
"$@"

if [ "${SCOREBOARD_ONLY:-0}" = "1" ]; then
  PY=python3
  if [ -x pipeline/vision/.venv/bin/python ]; then
    PY=pipeline/vision/.venv/bin/python
  fi
  FILM_GAME=$GAME_ID
  if [ "${SKIP_GAME_ID:-0}" = "1" ]; then
    FILM_GAME=
  fi
  "$PY" scripts/fallback/scoreboard_film.py --clip "$CLIP" --game-id "$FILM_GAME"
else
  python3 -m pipeline.present --clip "$CLIP"
  if python3 -m pipeline.present.tracks --clip "$CLIP"; then
    echo "中英文音轨已生成"
  else
    echo "音轨没做成。先交记分牌成片，稍后再补 assets/${CLIP}-zh.mp4 和 assets/${CLIP}-en.mp4"
  fi
fi

FILM="web/assets/${CLIP}-broadcast.mp4"
if [ ! -f "$FILM" ]; then
  echo "没有成片 $FILM" >&2
  exit 1
fi

WEB=$(aws cloudformation describe-stacks --stack-name "$STACK" --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebBucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name "$STACK" --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
if [ -z "$WEB" ] || [ "$WEB" = "None" ] || [ -z "$DIST" ] || [ "$DIST" = "None" ]; then
  echo "查不到栈 $STACK 的网页桶或 CloudFront 分布。成片留在 $FILM" >&2
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
if aws s3 cp "s3://${WEB}/replay/clips.js" "$TMP/remote.js" --profile "$PROFILE" --region "$REGION"; then
  :
else
  : > "$TMP/remote.js"
fi
python3 scripts/fallback/merge_lead.py "$CLIP" web/replay/clips.js "$TMP/remote.js" "$TMP/clips.js"
cp "$TMP/clips.js" web/replay/clips.js

aws s3 cp "$FILM" "s3://${WEB}/assets/${CLIP}-broadcast.mp4" \
  --content-type video/mp4 --profile "$PROFILE" --region "$REGION"
PATHS="/assets/${CLIP}-broadcast.mp4 /replay/clips.js"
for name in "${CLIP}-replay.mp4" "${CLIP}-zh.mp4" "${CLIP}-en.mp4"; do
  if [ -f "web/assets/$name" ]; then
    aws s3 cp "web/assets/$name" "s3://${WEB}/assets/$name" \
      --content-type video/mp4 --profile "$PROFILE" --region "$REGION"
    PATHS="$PATHS /assets/$name"
  fi
done
aws s3 cp "$TMP/clips.js" "s3://${WEB}/replay/clips.js" \
  --content-type "application/javascript; charset=utf-8" --profile "$PROFILE" --region "$REGION"
# 路径之间是空格，没有空格进文件名。不要改成带引号的一整串，否则失效会当成一条路径。
# shellcheck disable=SC2086
aws cloudfront create-invalidation --distribution-id "$DIST" --paths $PATHS \
  --profile "$PROFILE" --region "$REGION" >/dev/null

echo "已上传并只失效：$PATHS"
echo "提交链接：https://d2vzd2batw1eqe.cloudfront.net/"
echo "失效通常一两分钟。用无痕窗口打开根路径，应看到片名 ${CLIP}。"
echo "自检：python3 scripts/qa/check_output.py ${CLIP} --clip ${CLIP}"
