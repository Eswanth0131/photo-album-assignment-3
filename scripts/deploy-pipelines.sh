#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
: "${CONNECTION_ARN:?Set CONNECTION_ARN to an AVAILABLE GitHub connection ARN}"
: "${BACKEND_REPO:?Set BACKEND_REPO to owner/repository}"
: "${FRONTEND_REPO:?Set FRONTEND_REPO to owner/repository}"
app="${APP_STACK:-photo-album-a3}"
stack="${PIPELINE_STACK:-photo-album-a3-ci}"
bucket="$(stack_output "$app" WebsiteBucket)"
api="$(stack_output "$app" ApiId)"
python3 infra/generate.py --check
role_option=()
if [[ -n "${CFN_EXECUTION_ROLE_ARN:-}" ]]; then
  role_option=(--role-arn "$CFN_EXECUTION_ROLE_ARN")
fi
aws cloudformation deploy "${role_option[@]+${role_option[@]}}" --stack-name "$stack" --template-file infra/pipelines.json \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "ConnectionArn=$CONNECTION_ARN" \
  "BackendRepository=$BACKEND_REPO" "FrontendRepository=$FRONTEND_REPO" \
  "BackendBranch=${BACKEND_BRANCH:-main}" "FrontendBranch=${FRONTEND_BRANCH:-main}" \
  "WebsiteBucket=$bucket" "ApiId=$api"
