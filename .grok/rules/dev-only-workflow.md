# Dev-only workflow (active)

Deploy to dev without a separate approval. Production stays blocked until the user explicitly approves a promote.

1. **Deploy to dev AWS** (e.g. `stem-study-api-dev`, dev S3 frontend, pool `ap-southeast-1_h3BqT4uTs`) as part of finishing the work.
2. **Push only to dev GitHub**: `https://github.com/beawizard/stem-study.git` (`origin` / `main`).
3. **Do not** deploy to `StemStudy-prod`, prod S3, or prod Cognito/API.
4. **Do not** push to `prod` remote / `stem-study-prod` unless the user clearly requests a prod promote.

Dev frontend: S3 website HTTP URL for the dev bucket.  
Prod is frozen until a deliberate migration/promote request.
