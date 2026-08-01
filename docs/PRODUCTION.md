# Production (no Route53)

## Important: custom names like `stem-melon.com`

AWS does **not** give you a free human-friendly domain such as `stem-melon.com`.  
That name only works after you **buy/register** it at a registrar.

What AWS **does** give you immediately (HTTPS, no domain purchase, **no Route53**):

```text
https://d1lnlrb5tt3gib.cloudfront.net
```

That is the production SPA URL today.

Later, when you own `stem-melon.com`:

1. Request an ACM certificate in **us-east-1** for `stem-melon.com`
2. At the registrar, add the ACM **DNS validation CNAME**
3. Add `stem-melon.com` **CNAME** (or ALIAS) → `d1lnlrb5tt3gib.cloudfront.net`
4. Attach the cert + alias to the CloudFront distribution

Still **no Route53 required**.

---

## Current prod endpoints

| Resource | Value |
|----------|--------|
| **Frontend (HTTPS)** | https://d1lnlrb5tt3gib.cloudfront.net |
| **API** | https://exzb4p1zb4.execute-api.ap-southeast-1.amazonaws.com |
| **Table** | `stem-study-prod` |
| **Cognito pool** | `ap-southeast-1_360K0Xuhb` |
| **GitHub prod** | https://github.com/beawizard/stem-study-prod.git |
| **GitHub dev** | https://github.com/beawizard/stem-study.git |

Dev remains separate (HTTP S3 website + `stem-study` repo).

---

## Architecture (low cost)

```text
Browser → https://*.cloudfront.net (CloudFront, Price Class 100)
       → S3 (static SPA)

Browser → API Gateway HTTP API
       → Lambda → DynamoDB + Cognito
```

No Route53, no ALB, no EC2.

---

## Redeploy

```bash
source .venv/bin/activate
bash scripts/package_lambda.sh
cd infrastructure
npx aws-cdk@2 deploy StemStudy-prod \
  -c env=prod -c region=ap-southeast-1 -c account=940307563376 \
  -c frontendDomain= \
  --require-approval never --outputs-file ../cdk-outputs-prod.json
```

Then re-apply frontend config with outputs and `aws s3 sync` + CloudFront invalidation (see `scripts/deploy_prod.sh`).

## Re-migrate data

```bash
python scripts/migrate_dev_to_prod.py \
  --source-table stem-study-dev \
  --dest-table stem-study-prod \
  --source-pool ap-southeast-1_h3BqT4uTs \
  --dest-pool <PROD_POOL_ID> \
  --region ap-southeast-1
```

Temporary Cognito passwords: `scripts/.migrate-temp-passwords.json` (gitignored).
