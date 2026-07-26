# Custom domain: https://melon-dev.com

Development SPA hostname is **https://melon-dev.com**.  
Source of truth for code remains **https://github.com/beawizard/stem-study.git**.

## What AWS creates

| Resource | Purpose |
|----------|---------|
| Route53 public hosted zone `melon-dev.com` | DNS for the app |
| ACM certificate (us-east-1) | HTTPS for CloudFront |
| CloudFront distribution | Serves S3 frontend over HTTPS |
| A / AAAA aliases (`melon-dev.com`, `www.melon-dev.com`) | Point domain at CloudFront |

S3 website hosting is kept as an **HTTP fallback** (legacy bucket URL).

## Deploy

```bash
# Bootstrap both regions (once)
npx aws-cdk bootstrap aws://940307563376/ap-southeast-1
npx aws-cdk bootstrap aws://940307563376/us-east-1

cd infrastructure
source ../.venv/bin/activate
pip install -r requirements.txt
bash ../scripts/package_lambda.sh   # keep Lambda package current

npx aws-cdk deploy StemStudy-dev \
  -c env=dev \
  -c region=ap-southeast-1 \
  -c account=940307563376 \
  -c frontendDomain=melon-dev.com \
  --require-approval never
```

## IONOS nameserver cutover (required)

`melon-dev.com` is currently on IONOS nameservers (`ui-dns.*`).  
ACM cannot issue the certificate until the domain’s **authoritative NS** are the Route53 zone NS.

### Route53 nameservers (hosted zone `Z05012802ZVC9DGESW848`)

Set **exactly** these four at IONOS (custom nameservers):

```
ns-113.awsdns-14.com
ns-865.awsdns-44.net
ns-1158.awsdns-16.org
ns-1581.awsdns-05.co.uk
```

1. IONOS → Domains → `melon-dev.com` → Nameservers → **Use custom nameservers**.
2. Paste the four AWS NS values above (no trailing period needed in most UIs).
3. Save. Propagation is often 15–60 minutes (can be up to 48h).
4. CDK deploy will finish ACM validation + CloudFront once DNS points here.

Check:

```bash
dig NS melon-dev.com +short
# expect awsdns nameservers (not ui-dns.*)

curl -sI https://melon-dev.com | head -5
```

## App config

`frontend/index.html`:

```js
window.STEM_CONFIG = {
  apiUrl: "https://6bg1skwdm6.execute-api.ap-southeast-1.amazonaws.com",
  // …
  frontendUrl: "https://melon-dev.com",
};
```

API stays on API Gateway; only the static frontend moves to the custom domain.

## Disable custom domain

```bash
npx aws-cdk deploy StemStudy-dev -c frontendDomain=
```

(Empty / omit context; for non-dev envs the domain is off unless set.)
