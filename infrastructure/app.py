#!/usr/bin/env python3
"""STEM Study App – CDK app entrypoint."""

import aws_cdk as cdk

from stem_stack import StemStack

app = cdk.App()

env_name = app.node.try_get_context("env") or "dev"
account = app.node.try_get_context("account")
region = app.node.try_get_context("region") or "ap-southeast-1"

StemStack(
    app,
    f"StemStudy-{env_name}",
    env_name=env_name,
    env=cdk.Environment(
        account=account,
        region=region,
    ),
    description=f"STEM Study serverless app ({env_name})",
)

app.synth()
