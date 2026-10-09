#!/bin/sh
# 在用户池里建一个评委账号。密码从环境变量读，不要写进仓库。
set -eu
profile=${AWS_PROFILE:-team04}
pool=${USER_POOL_ID:?设置 USER_POOL_ID}
user=${JUDGE_USER:?设置 JUDGE_USER}
password=${JUDGE_PASSWORD:?设置 JUDGE_PASSWORD，不要写进命令历史之外的文件}
aws cognito-idp admin-create-user \
  --profile "$profile" \
  --region us-east-1 \
  --user-pool-id "$pool" \
  --username "$user" \
  --message-action SUPPRESS
aws cognito-idp admin-set-user-password \
  --profile "$profile" \
  --region us-east-1 \
  --user-pool-id "$pool" \
  --username "$user" \
  --password "$password" \
  --permanent
