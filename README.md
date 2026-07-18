# STEM Study

Secure, cost-efficient **serverless** web study app for low traffic (&lt; 10k requests/day).  
Built for mobile/tablet form factors. Target infrastructure cost for ~1,000 users: **under ~$10/month**.

## Architecture

| Component | Choice | Why |
|-----------|--------|-----|
| Compute | AWS Lambda **ARM64 (Graviton)** | ~20% cheaper than x86 |
| API | API Gateway **HTTP API** + JWT authorizer | Lower cost than REST API |
| Data | **DynamoDB** single-table, on-demand | No idle cost; no RDS |
| Auth | **Amazon Cognito** | Free tier 50k MAU |
| Frontend | **S3** static website | Pennies at low traffic |
| IaC | **AWS CDK (Python)** | Repeatable deploys |

```
Browser (SPA) → Cognito (sign-up/login)
             → HTTP API (JWT) → Lambda (Python 3.12, arm64)
                              → DynamoDB (single table)
```

No VPC, NAT, or relational DB — those would dominate cost at this scale.

### DynamoDB single-table keys

| Entity | PK | SK |
|--------|----|----|
| User profile | `USER#<sub>` | `META` |
| Task | `USER#<sub>` | `TASK#<id>` |
| Progress | `USER#<sub>` | `PROGRESS#<subject>#L#<level>` |
| Session / Attempt | `USER#<sub>` | `SESSION#…` / `ATTEMPT#…` |
| Payment | `USER#<sub>` | `PAYMENT#<id>` |
| Subject / Level / Question | `SUBJECT#<id>` | `META` / `LEVEL#…` / `LEVEL#…#Q#…` |

GSI1 supports admin listings (`ENTITY#SUBJECT`, `ENTITY#PAYMENT`, …).

## Features

1. **Auth** – Cognito email sign-up; 1‑month trial on first login.
2. **Tasks** – CRUD, **owner-only**, **soft delete**.
3. **Subjects** – Math seeded by default; admins can add subjects later.
4. **Levels & CSV questions** – Admin defines levels; upload CSV such as `1,+,2,=,3`.
5. **Progression** – Complete a level (accuracy ≥ pass threshold) to unlock the next.
6. **Insights** – Accuracy + time → rule-based study recommendations.
7. **GCash** – After trial, user submits GCash reference; admin verifies → +30 days access.

### Admin

Add user to Cognito group `admin`, then:

```http
POST /admin/seed
POST /subjects
POST /subjects/{id}/levels
POST /subjects/{id}/levels/{lid}/questions   # body: text/csv or {"csv":"..."}
GET  /admin/payments
POST /admin/payments/{user_id}/{payment_id}/verify  # {"status":"verified"|"rejected"}
```

## Project layout

```
backend/app/          # Lambda code (handler, services, validation)
frontend/             # Mobile-first SPA (HTML/CSS/JS + Cognito)
infrastructure/       # AWS CDK (Python)
tests/                # pytest + moto (unit + integration)
```

## Local development

### Prerequisites

- Python 3.12+ (3.11+ OK for tests)
- Node.js 18+ (for AWS CDK CLI)
- AWS CLI credentials (for deploy only)

### Install & test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Run unit + integration tests with coverage (fail under 80%)
pytest
```

Tests use **moto** to mock DynamoDB. Set `ALLOW_TEST_AUTH=1` is applied by fixtures so handlers accept `X-Test-User` / `X-Test-Groups` headers (never enable in production).

### Deploy

```bash
npm install -g aws-cdk
cd infrastructure
pip install -r requirements.txt
cdk bootstrap   # once per account/region
cdk deploy -c env=dev -c region=ap-southeast-1
```

After deploy, copy stack outputs into `frontend/index.html` → `window.STEM_CONFIG`:

```js
window.STEM_CONFIG = {
  apiUrl: "https://xxxx.execute-api.ap-southeast-1.amazonaws.com",
  userPoolId: "ap-southeast-1_XXXX",
  userPoolClientId: "xxxx",
  region: "ap-southeast-1",
  gcashMerchant: "09XX-XXX-XXXX",
  monthlyPricePhp: 99
};
```

Redeploy frontend (or re-run `cdk deploy`) so S3 picks up config. Promote an admin:

```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id <UserPoolId> \
  --username <email> \
  --group-name admin
```

Then call `POST /admin/seed` once.

## Cost notes (order-of-magnitude, ap-southeast-1)

| Service | Low-traffic estimate |
|---------|----------------------|
| Lambda (arm64, 256 MB) | &lt; $1 |
| HTTP API | &lt; $1 |
| DynamoDB on-demand | $1–3 |
| Cognito | $0 (within free tier) |
| S3 | &lt; $1 |
| **Total** | **typically well under $10** |

Watch: accidental high Lambda memory/timeout, CloudWatch excessive logs retention, and enabling Cognito advanced security (extra $).

## Security

- Cognito JWT on all non-health routes
- Owner-scoped task access; admin group for mutations
- Soft delete only (no hard purge in app layer)
- Pydantic input validation; least-privilege IAM (`grant_read_write_data` on one table)
- CORS enabled for SPA; tighten `allow_origins` for production domains

## License

MIT
