#!/usr/bin/env bash
# Deploy StemStudy-prod and optionally migrate data from dev.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
REGION="${AWS_REGION:-ap-southeast-1}"
ACCOUNT="${CDK_ACCOUNT:-940307563376}"
# Leave empty for free HTTPS on *.cloudfront.net (no Route53, no domain purchase).
# Later, when you own a domain: register it, then CNAME to CloudFront (still no Route53).
DOMAIN="${FRONTEND_DOMAIN:-}"

echo "==> Package Lambda"
bash scripts/package_lambda.sh

echo "==> CDK bootstrap"
npx --yes aws-cdk@2 bootstrap "aws://${ACCOUNT}/${REGION}"

echo "==> CDK deploy StemStudy-prod (CloudFront HTTPS; custom domain=${DOMAIN:-none})"
cd infrastructure
npx --yes aws-cdk@2 deploy StemStudy-prod \
  -c "env=prod" \
  -c "region=${REGION}" \
  -c "account=${ACCOUNT}" \
  -c "frontendDomain=${DOMAIN}" \
  --require-approval never \
  --outputs-file ../cdk-outputs-prod.json

echo "==> Read outputs"
python3 - <<'PY'
import json
from pathlib import Path
p = Path("../cdk-outputs-prod.json")
if not p.exists():
    # fallback flat
    print("No outputs file")
    raise SystemExit(1)
raw = json.loads(p.read_text())
# CDK nests under stack name
outs = raw.get("StemStudy-prod") or raw.get(list(raw.keys())[0])
for k in sorted(outs):
    print(f"{k}={outs[k]}")
Path("/tmp/stem-prod-outs.env").write_text(
    "\n".join(f"{k}={v}" for k, v in outs.items()) + "\n"
)
PY

# shellcheck disable=SC1091
source /tmp/stem-prod-outs.env

echo "==> Write frontend STEM_CONFIG for prod (restore dev index after upload)"
cd "$ROOT"
cp frontend/index.html /tmp/stem-index-dev.html
python3 scripts/write_frontend_config.py \
  --api-url "${HttpApiUrl}" \
  --user-pool-id "${UserPoolId}" \
  --user-pool-client-id "${UserPoolClientId}" \
  --region "${Region:-$REGION}" \
  --frontend-url "${FrontendUrl}"

echo "==> Sync frontend to prod S3"
aws s3 sync frontend/ "s3://${FrontendBucketName}/" --region "$REGION" --exclude "*.DS_Store"
cp /tmp/stem-index-dev.html frontend/index.html
if [[ -n "${CloudFrontDistributionId:-}" ]]; then
  echo "==> Invalidate CloudFront"
  aws cloudfront create-invalidation --distribution-id "$CloudFrontDistributionId" --paths "/*" >/dev/null || true
fi

echo "==> Done deploy. Outputs:"
cat /tmp/stem-prod-outs.env
echo
echo "Prod SPA (HTTPS, no custom domain purchase): ${CloudFrontHttpsUrl:-$FrontendUrl}"
echo "No Route53. To use stem-melon.com later: buy the domain, CNAME it to CloudFrontDomainName."
echo
echo "Migrate data: python scripts/migrate_dev_to_prod.py --source-pool <dev-pool> --dest-pool ${UserPoolId}"
