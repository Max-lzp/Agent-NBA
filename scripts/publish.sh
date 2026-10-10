#!/bin/sh
# 生成评委成片，并在 team04 账号可用时部署。
# 提交链接是 CloudFront 根路径，打开的是回放页，不是上传页。
# GPU 配额为 0，所以关掉 Batch。Polly 若仍拒绝，解说留在已经混好的本地成片里，所以关掉 P2。
set -eu
cd "$(dirname "$0")/.."
python3 -m pipeline.present --all
echo "本地回放：web/game.html"
echo "成片：web/assets/*-broadcast.mp4"
if ! aws sts get-caller-identity --profile team04 --region us-east-1 >/dev/null 2>&1; then
  echo "team04 账号还不能用。成片已经在本地。账号恢复后重跑本脚本。"
  exit 0
fi
cd cdk
npx cdk deploy Team04DeepCourt -c enableP1=false -c enableP2=false --profile team04 --require-approval never
echo "提交门户时填栈输出 CloudFrontUrl。打开应是回放页。同一域名 /readme 是作品说明。"
