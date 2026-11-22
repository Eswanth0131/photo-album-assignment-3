#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT
for implementation in index search; do
  cat backend/shared.py backend/"$implementation".py > "$temp_dir/index.py"
  (cd "$temp_dir" && zip -q "$implementation.zip" index.py)
done
for implementation in index search; do
  function_name="$implementation-photos"
  aws lambda update-function-code --function-name "$function_name" \
    --zip-file "fileb://$temp_dir/$implementation.zip" --query LastModified --output text
  aws lambda wait function-updated --function-name "$function_name"
done
