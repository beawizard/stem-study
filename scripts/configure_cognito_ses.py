#!/usr/bin/env python3
"""Wire Cognito user pool email sending to Amazon SES (better deliverability).

Cognito DEFAULT mail (no-reply@verificationemail.com) often lands in Spam or
is delayed. SES with a verified From address is more reliable.

Prerequisites
-------------
1. Verify From email (or domain) in SES in the same region as the pool:
     aws ses verify-email-identity --email-address you@example.com --region ap-southeast-1
   Click the link SES emails you.

2. SES sandbox: you can only send *to* verified addresses until production access.
   Verify test recipients the same way, or request SES production access.

Usage
-----
  python scripts/configure_cognito_ses.py \\
    --pool-id ap-southeast-1_h3BqT4uTs \\
    --from-email you@example.com \\
    --region ap-southeast-1
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pool-id", required=True)
    p.add_argument("--from-email", required=True)
    p.add_argument("--from-name", default="MElon Basic Education")
    p.add_argument("--region", default="ap-southeast-1")
    p.add_argument("--role-name", default="")
    args = p.parse_args()

    ses = boto3.client("ses", region_name=args.region)
    cognito = boto3.client("cognito-idp", region_name=args.region)
    iam = boto3.client("iam")
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    from_email = args.from_email.strip().lower()
    role_name = args.role_name or f"stem-cognito-ses-{args.pool_id.replace('_', '-')[-12:]}"

    # 1) SES identity must be verified
    attrs = ses.get_identity_verification_attributes(Identities=[from_email])
    status = (
        attrs.get("VerificationAttributes", {})
        .get(from_email, {})
        .get("VerificationStatus")
    )
    if status != "Success":
        print(f"SES identity {from_email!r} status={status!r} (need Success).")
        print("Sending verification email (if not already)…")
        ses.verify_email_identity(EmailAddress=from_email)
        print(
            f"Open the Amazon SES verification link sent to {from_email}, "
            "then re-run this script."
        )
        return 2

    # 2) IAM role Cognito can assume
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "cognito-idp.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"sts:ExternalId": args.pool_id}
                },
            }
        ],
    }
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Cognito → SES for password reset / verification emails",
        )
        print(f"Created role {role_name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        iam.update_assume_role_policy(
            RoleName=role_name, PolicyDocument=json.dumps(trust)
        )
        print(f"Updated trust on role {role_name}")

    source_arn = f"arn:aws:ses:{args.region}:{account}:identity/{from_email}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ses:SendEmail", "ses:SendRawEmail"],
                "Resource": [source_arn],
            }
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="CognitoSesSend",
        PolicyDocument=json.dumps(policy),
    )
    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

    # 3) Attach to Cognito (must pass full config blocks Cognito requires)
    pool = cognito.describe_user_pool(UserPoolId=args.pool_id)["UserPool"]
    kwargs = {
        "UserPoolId": args.pool_id,
        "EmailConfiguration": {
            "SourceArn": source_arn,
            "EmailSendingAccount": "DEVELOPER",
            "From": f"{args.from_name} <{from_email}>",
        },
        "AutoVerifiedAttributes": pool.get("AutoVerifiedAttributes") or ["email"],
        "Policies": pool.get("Policies") or {},
    }
    # Preserve MFA / recovery if present
    if pool.get("MfaConfiguration"):
        kwargs["MfaConfiguration"] = pool["MfaConfiguration"]
    if pool.get("AccountRecoverySetting"):
        kwargs["AccountRecoverySetting"] = pool["AccountRecoverySetting"]

    cognito.update_user_pool(**kwargs)
    print("Updated Cognito EmailConfiguration → SES DEVELOPER")
    print(f"  From: {args.from_name} <{from_email}>")
    print(f"  Role: {role_arn}")
    print(f"  SourceArn: {source_arn}")
    print(
        "If SES is still in sandbox, verify each recipient email with "
        "ses verify-email-identity, or request SES production access."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
