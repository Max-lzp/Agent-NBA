#!/bin/bash
# 在已经开着的 Batch 主机上把依赖、libxcb、ffmpeg 和两份权重打进 ECR。
# 本机没有 Docker。不要把这段写进启动模板，避免盖掉 Batch 注册 ECS 的用户数据。
# 仓库里的 inference/Dockerfile 按仓库根目录构建，给 CDK -c buildTrackerImage=true 用。
# 这里仍同步 S3 bootstrap/bake/ 的扁平目录。不要把新 Dockerfile 直接覆盖进那个目录。
set -euo pipefail
BUCKET="${BUCKET:?}"
REPO="${REPO:?}"
REGION="${REGION:-us-east-1}"
LOG=/var/log/deepcourt-bake.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
trap 'aws s3 cp "$LOG" "s3://$BUCKET/bootstrap/bake.log" --region "$REGION" || true' EXIT

if [[ "${FORCE:-0}" != "1" ]] && aws ecr describe-images --repository-name deepcourt-tracker --image-ids imageTag=ready --region "$REGION" >/dev/null 2>&1; then
  echo "ready 已在仓库里"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  dnf install -y docker
fi
systemctl enable --now docker

WORKDIR=/opt/deepcourt-bake
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR/models" "$WORKDIR/web" "$WORKDIR/vision"
aws s3 sync "s3://$BUCKET/bootstrap/bake/" "$WORKDIR" --region "$REGION"
aws s3 cp "s3://$BUCKET/bootstrap/models/player_detector.pt" "$WORKDIR/models/player_detector.pt" --region "$REGION"
aws s3 cp "s3://$BUCKET/bootstrap/models/ball_detector_model.pt" "$WORKDIR/models/ball_detector_model.pt" --region "$REGION"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build -t "$REPO:ready" "$WORKDIR"
docker push "$REPO:ready"
echo BAKED
