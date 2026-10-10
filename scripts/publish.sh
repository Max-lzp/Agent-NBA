#!/bin/sh
# 生成评委成片，并部署到 CloudFront。
# 提交链接是 CloudFront 根路径，打开的是回放页，不是上传页。发布台在 /hub.html。
# 追踪用 c7i.48xlarge，P1 留在栈里。Polly 若仍拒绝，解说留在已经混好的本地成片里，所以关掉 P2。
# 可部署账号是 Workshop 参赛账号（profile nba → 382918437747）。SSO team04 目前不能建栈。
set -eu
cd "$(dirname "$0")/.."
python3 -m pipeline.present --all
echo "本地回放：web/game.html"
echo "发布台：web/hub.html"
echo "成片：web/assets/*-broadcast.mp4"

PROFILE=nba
if ! aws sts get-caller-identity --profile "$PROFILE" --region us-east-1 >/dev/null 2>&1; then
  echo "profile nba 还不能用。成片已经在本地。恢复后重跑本脚本。"
  exit 0
fi
ACCOUNT=$(aws sts get-caller-identity --profile "$PROFILE" --region us-east-1 --query Account --output text)
export CDK_DEFAULT_ACCOUNT="$ACCOUNT"
export CDK_DEFAULT_REGION=us-east-1
cd cdk
npx cdk deploy Team04DeepCourt -c enableP2=false --profile "$PROFILE" --require-approval never
echo "提交门户时填栈输出 CloudFrontUrl（根路径=回放页）。发布台：同一域名 /hub.html。/readme 是作品说明。"
