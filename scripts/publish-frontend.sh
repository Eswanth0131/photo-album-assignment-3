#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
api_id="${1:?API ID is required}"
bucket="${2:?Website bucket is required}"
temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT
aws apigateway get-sdk --rest-api-id "$api_id" --stage-name v1 \
  --sdk-type javascript "$temp_dir/sdk.zip"
unzip -q "$temp_dir/sdk.zip" -d "$temp_dir/sdk"
sdk_client="$(find "$temp_dir/sdk" -name apigClient.js -type f -print | head -n 1)"
test -n "$sdk_client"
mkdir "$temp_dir/site"
cp frontend/index.html frontend/app.js frontend/style.css "$temp_dir/site/"
mkdir "$temp_dir/site/sdk"
sdk_dir="$(dirname "$sdk_client")"
cp "$sdk_client" "$temp_dir/site/sdk/"
cp -R "$sdk_dir/lib" "$temp_dir/site/sdk/"
python3 - "$api_id" "$AWS_DEFAULT_REGION" "$temp_dir/site/config.js" <<'PY'
import json
import sys
from pathlib import Path
api, region, path = sys.argv[1:]
suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
Path(path).write_text("window.albumConfig = " + json.dumps({
    "apiUrl": f"https://{api}.execute-api.{region}.{suffix}/v1",
}) + ";\n")
PY
# Publish dependencies before the HTML that references them.
aws s3 cp "$temp_dir/site/" "s3://$bucket/" --recursive --exclude index.html \
  --cache-control no-cache --only-show-errors
aws s3 cp "$temp_dir/site/index.html" "s3://$bucket/index.html" \
  --cache-control no-cache --only-show-errors
