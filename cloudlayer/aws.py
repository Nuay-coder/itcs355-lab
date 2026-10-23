"""AWS adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install boto3
Docs: S3 -> boto3 client("s3"); ECR -> boto3 client("ecr") for the auth token,
      then `docker push` through subprocess.

Hints for Lab 1:
  * BLOB_URI looks like s3://bucket/prefix — parse it here, never in src/.
  * ECR login expires. If a push that worked yesterday fails today, re-authenticate:
        aws ecr get-login-password --region $REGION | docker login --username AWS \
            --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
  * Return the DIGEST reference from push_image, not the tag. `docker inspect` or the
    push output gives you the sha256.
  * Tag the bucket objects and the ECR repository with cfg.tags(1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cloudlayer.base import CloudAdapter

# Lab 5 Task 4B: the three methods only. No persistent endpoint exists on AWS (that's
# a full Lab 1-3 migration, explicitly out of scope — "you are not migrating the whole
# system"), so `invoke` here calls a Bedrock on-demand model directly rather than a
# SageMaker real-time endpoint. Claude 3 Haiku is the cheapest model this account's
# region (ap-southeast-1) supports ON_DEMAND invoke for, without provisioning anything.
_BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri.removeprefix("s3://")
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


class AwsAdapter(CloudAdapter):
    def upload(self, local_path: str, key: str) -> str:
        import boto3

        bucket_name, prefix = _split_s3_uri(self.cfg.blob_uri)
        full_key = f"{prefix.rstrip('/')}/{key}" if prefix else key
        boto3.client("s3").upload_file(local_path, bucket_name, full_key)
        return f"s3://{bucket_name}/{full_key}"

    def download(self, uri: str, local_path: str) -> None:
        import boto3

        bucket_name, key = _split_s3_uri(uri)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        boto3.client("s3").download_file(bucket_name, key, local_path)

    def push_image(self, local_tag: str) -> str:
        raise NotImplementedError("TODO Lab 1: authenticate to ECR, push, return repo@sha256:...")

    def invoke(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Lab 5 Task 4B widening: `endpoint` is accepted for interface parity with the
        CloudAdapter signature (and with what portability_swap_check.py passes every
        adapter uniformly) but unused — there is no per-request routing concept for a
        Bedrock on-demand model the way there is for a SageMaker/Cloud Run endpoint
        name. The model id is fixed above, not derived from `endpoint` or `payload`.
        """
        import json as jsonlib

        import boto3

        client = boto3.client("bedrock-runtime", region_name=self.cfg.region or "ap-southeast-1")
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 32,
            "messages": [{
                "role": "user",
                "content": "Reply with exactly the two words: portability confirmed",
            }],
        }
        response = client.invoke_model(modelId=_BEDROCK_MODEL_ID, body=jsonlib.dumps(body))
        return jsonlib.loads(response["body"].read())

    # submit_training / register_model  -> Lab 2 (SageMaker training job + model package group)
    # deploy                            -> Lab 3 (SageMaker real-time endpoint)
    # emit_metric                       -> Lab 4 (CloudWatch put_metric_data)
    # generate                          -> Lab 5 (managed LLM endpoint; read the usage block for tokens)
    # teardown                          -> Lab 5 (resourcegroupstaggingapi to find by tag)
