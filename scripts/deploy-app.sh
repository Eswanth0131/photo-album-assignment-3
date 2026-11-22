#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
mode="${1:-full}"
case "$mode" in
  full) enable=true ;;
  minimum) enable=false ;;
  *) echo 'Usage: scripts/deploy-app.sh [full|minimum]' >&2; exit 2 ;;
esac
stack="${APP_STACK:-photo-album-a3}"
account="$(aws sts get-caller-identity --query Account --output text)"
bucket="${PHOTO_BUCKET_NAME:-photo-album-a3-${account}-${AWS_DEFAULT_REGION}}"
python3 infra/generate.py
role_option=()
if [[ -n "${CFN_EXECUTION_ROLE_ARN:-}" ]]; then
  role_option=(--role-arn "$CFN_EXECUTION_ROLE_ARN")
fi
aws cloudformation deploy "${role_option[@]+${role_option[@]}}" --stack-name "$stack" --template-file infra/application.json \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "PhotoBucketName=$bucket" "EnableSearch=$enable"
api_id="$(stack_output "$stack" ApiId)"
website_bucket="$(stack_output "$stack" WebsiteBucket)"
scripts/publish-frontend.sh "$api_id" "$website_bucket"
printf 'Website: %s\n' "$(stack_output "$stack" WebsiteUrl)"
printf 'Run scripts/show-api-key.sh %s to retrieve the demo API key.\n' "$stack"
