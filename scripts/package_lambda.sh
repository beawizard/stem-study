#!/usr/bin/env bash
# Build backend/lambda_package for CDK deploy (no Docker required).
# Targets Lambda Python 3.12 on arm64 (Graviton).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/backend/lambda_package"
REQ="$ROOT/backend/requirements-lambda.txt"

rm -rf "$OUT"
mkdir -p "$OUT"

python3 -m pip install --upgrade -q pip
# manylinux aarch64 wheels so the package runs on Lambda ARM, not macOS.
python3 -m pip install --no-cache-dir \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  -r "$REQ" \
  -t "$OUT"

cp -R "$ROOT/backend/app" "$OUT/app"
find "$OUT" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true

echo "Lambda package ready: $OUT"
du -sh "$OUT"
ls "$OUT" | head
