# Production: https://stem.melon.com

Low-cost AWS setup: **S3 + CloudFront (Price Class 100) + ACM + Route53 zone for the subdomain** + existing serverless API pattern.

| | Dev | Prod |
|--|-----|------|
| Frontend | S3 website HTTP | **https://stem.melon.com** |
| Stack | `StemStudy-dev` | `StemStudy-prod` |
| Table | `stem-study-dev` | `stem-study-prod` |
| Lambda | `stem-study-api-dev` | `stem-study-api-prod` |
| GitHub (dev) | https://github.com/beawizard/stem-study.git | promote to **stem-study-prod** when ready |

## Deploy prod stack

```bash
source .venv/bin/activate
chmod +x scripts/deploy_prod.sh
./scripts/deploy_prod.sh
```

Or:

```bash
bash scripts/package_lambda.sh
cd infrastructure
npx aws-cdk@2 deploy StemStudy-prod \
  -c env=prod -c region=ap-southeast-1 -c account=940307563376 \
  -c frontendDomain=stem.melon.com \
  --outputs-file ../cdk-outputs-prod.json
```

## DNS (required for HTTPS)

Parent domain `melon.com` is currently on **Kakao DNS**. The stack creates a Route53 hosted zone for **`stem.melon.com` only**.

At the **melon.com** DNS provider, add **NS records** for hostname `stem` (or `stem.melon.com`) pointing to stack output **HostedZoneNameServers** (four `awsdns` names).

Until those NS are live:

- ACM certificate stays pending
- `https://stem.melon.com` will not resolve through CloudFront

Check:

```bash
dig NS stem.melon.com +short   # expect awsdns
curl -sI https://stem.melon.com | head -5
```

## Migrate data (Math topics/levels + users)

After prod stack exists:

```bash
# From cdk-outputs / AWS console:
# DEV_POOL=ap-southeast-1_h3BqT4uTs
# PROD_POOL=<UserPoolId from StemStudy-prod>
# PROD_TABLE=stem-study-prod

python scripts/migrate_dev_to_prod.py \
  --source-table stem-study-dev \
  --dest-table stem-study-prod \
  --source-pool ap-southeast-1_h3BqT4uTs \
  --dest-pool "$PROD_POOL" \
  --region ap-southeast-1
```

- Copies **all** DynamoDB items by default (subjects/levels/questions, schools, user profiles & progress).
- Cognito: creates users in prod; **passwords cannot be exported** → temporary passwords in `scripts/.migrate-temp-passwords.json` (**do not commit**).
- Re-run is mostly idempotent; use `--force-cognito` to reset passwords again.

Content-only (no USER# progress):

```bash
python scripts/migrate_dev_to_prod.py ... --content-only --skip-cognito
```

## Cost notes

- ACM: free  
- CloudFront Price Class 100: usually low dollars at light traffic  
- Route53 zone for `stem.melon.com`: **$0.50/mo**  
- Second stack (Lambda/API/DynamoDB): similar to dev  

Target: stay near **&lt; $10/mo** at low traffic.

## GitHub prod repo

Create https://github.com/beawizard/stem-study-prod.git and promote releases from dev:

```bash
git remote add prod git@github.com:beawizard/stem-study-prod.git   # once
git push prod main:main   # after review
```

Prefer tagging on dev before promote.
