"""Azure adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install azure-storage-blob azure-identity azure-containerregistry
Docs: BlobServiceClient for storage; ACR push goes through `docker push` after
      `az acr login --name <registry>`.

Hints for Lab 1:
  * BLOB_URI is either abfss://container@account.dfs.core.windows.net/prefix or
    https://account.blob.core.windows.net/container/prefix. Pick one form and parse
    it here, never in src/.
  * Use DefaultAzureCredential rather than a connection string. It picks up your CLI
    login locally and your managed identity in CI, which is what Lab 4 needs.
  * push_image must return the digest reference: registry.azurecr.io/repo@sha256:...
  * Azure tags live on the resource, not the blob. Tag the storage account, the
    registry, and later the workspace with cfg.tags(1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cloudlayer.base import CloudAdapter


def _split_blob_https_uri(uri: str) -> tuple[str, str, str]:
    """`https://<account>.blob.core.windows.net/<container>/<key...>` -> (account, container, key)."""
    without_scheme = uri.removeprefix("https://")
    host, _, rest = without_scheme.partition("/")
    account = host.split(".", 1)[0]
    container, _, key = rest.partition("/")
    return account, container, key


class AzureAdapter(CloudAdapter):
    def upload(self, local_path: str, key: str) -> str:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        account, container, prefix = _split_blob_https_uri(self.cfg.blob_uri)
        full_key = f"{prefix.rstrip('/')}/{key}" if prefix else key
        client = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        with open(local_path, "rb") as f:
            client.get_blob_client(container=container, blob=full_key).upload_blob(f, overwrite=True)
        return f"https://{account}.blob.core.windows.net/{container}/{full_key}"

    def download(self, uri: str, local_path: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        account, container, key = _split_blob_https_uri(uri)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        client = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        with open(local_path, "wb") as f:
            f.write(client.get_blob_client(container=container, blob=key).download_blob().readall())

    def push_image(self, local_tag: str) -> str:
        raise NotImplementedError("TODO Lab 1: az acr login, push, return registry/repo@sha256:...")

    # Lab 5 Task 4B. The resource and deployment this invoke() call talks to.
    _FOUNDRY_ENDPOINT = "https://itcs355-lab5-foundry.cognitiveservices.azure.com/models"
    _FOUNDRY_MODEL = "phi-4-mini-instruct"

    def invoke(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Lab 5 Task 4B widening: `endpoint` is accepted for interface parity with the
        CloudAdapter signature (and with what portability_swap_check.py passes every
        adapter uniformly) but unused — there is no per-request routing concept for an
        Azure AI Foundry serverless deployment the way there is for a managed online
        endpoint; the deployment name is the `model=` argument below instead.

        Uses `AzureCliCredential`, not `DefaultAzureCredential` like `upload`/
        `download` — confirmed live that `DefaultAzureCredential`'s fallback chain
        hangs for over two minutes against this resource (presumably probing a
        managed-identity endpoint that isn't reachable from this machine) before ever
        reaching the CLI-login credential that actually works. `upload`/`download`
        hit the same chain and only ever needed the extra RBAC-propagation wait, not
        this hang — a genuine, call-shape-specific flakiness, not a blanket
        DefaultAzureCredential problem.
        """
        from azure.ai.inference import ChatCompletionsClient
        from azure.ai.inference.models import UserMessage
        from azure.identity import AzureCliCredential

        client = ChatCompletionsClient(
            endpoint=self._FOUNDRY_ENDPOINT,
            credential=AzureCliCredential(),
            credential_scopes=["https://cognitiveservices.azure.com/.default"],
        )
        response = client.complete(
            messages=[UserMessage(content="Reply with exactly the two words: portability confirmed")],
            model=self._FOUNDRY_MODEL,
        )
        return {"response": response.choices[0].message.content}

    # submit_training / register_model  -> Lab 2 (Azure ML command job + model registry)
    # deploy                            -> Lab 3 (managed online endpoint + deployment)
    # emit_metric                       -> Lab 4 (Azure Monitor custom metric)
    # generate                          -> Lab 5 (managed LLM endpoint; read the usage block for tokens)
    # teardown                          -> Lab 5 (resource graph query by tag)
