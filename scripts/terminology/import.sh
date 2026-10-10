#!/bin/sh
# 把篮球术语表导入 Amazon Translate。名称 deepcourt-basketball，区域 us-east-1。
# 方向是单向：第一列 en 是源语言，zh 是译文。已有同名表时整表覆盖。
# 本脚本不会在没有凭证时替你调用；跑之前确认 profile nba 指向参赛账号。
set -eu
ROOT=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
FILE="$ROOT/data/terminology/basketball-en-zh.csv"
NAME=deepcourt-basketball
REGION=us-east-1
PROFILE="${AWS_PROFILE:-nba}"

if [ ! -f "$FILE" ]; then
  echo "找不到 $FILE" >&2
  exit 1
fi

aws translate import-terminology \
  --name "$NAME" \
  --merge-strategy OVERWRITE \
  --terminology-data "Format=CSV,Directionality=UNI,File=fileb://${FILE}" \
  --region "$REGION" \
  --profile "$PROFILE"

echo "导入命令已提交。校验（仍用同一区域和 profile）："
echo "aws translate get-terminology --name $NAME --terminology-data-format CSV --region $REGION --profile $PROFILE"
echo "aws translate list-terminologies --region $REGION --profile $PROFILE --query \"TerminologyPropertiesList[?Name=='${NAME}']\""
echo "解说进程要带上这个表时：TRANSLATE_TERMINOLOGY=$NAME"
echo "表不存在或角色没有术语表权限时，音轨代码会去掉术语表再译一次，不会因此中断。"
