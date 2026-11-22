#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
key_id="$(stack_output "${1:-${APP_STACK:-photo-album-a3}}" ApiKeyId)"
aws apigateway get-api-key --api-key "$key_id" --include-value --query value --output text
