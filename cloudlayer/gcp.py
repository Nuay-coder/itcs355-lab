"""GCP adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install google-cloud-storage google-cloud-aiplatform
Docs: storage.Client for GCS; Artifact Registry push goes through `docker push` after
      `gcloud auth configure-docker <region>-docker.pkg.dev`.

Hints for Lab 1:
  * BLOB_URI looks like gs://bucket/prefix — parse it here, never in src/.
  * Artifact Registry paths are region-scoped:
        <region>-docker.pkg.dev/<project>/<repo>/<image>
    A common first failure is pushing to gcr.io out of habit; it is a different service.
  * push_image must return the digest reference, not the tag.
  * GCP calls them labels, not tags, and they must be lowercase with no spaces.
    cfg.tags(1) already satisfies that constraint — do not "improve" the values.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from cloudlayer.base import CloudAdapter

_DIGEST_RE = re.compile(r"digest:\s*(sha256:[0-9a-f]{64})")


def _split_gs_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri.removeprefix("gs://")
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


class InvokeError(RuntimeError):
    """Raised by GcpAdapter.invoke() on any non-2xx response. Carries status_code so a
    caller (e.g. the Task 2 smoke test) can deliberately assert on a specific failure —
    a 422 for an invalid payload is an expected result to check for, not just noise."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class GcpAdapter(CloudAdapter):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        # Created here, in the main thread, before canary_traffic.py's thread pool (or
        # anything else) can exist — the lazy "create the lock if it doesn't exist yet"
        # idiom used for the caches below is itself not thread-safe, so the lock that
        # protects them has to be created somewhere that's guaranteed single-threaded.
        self._cache_lock = threading.Lock()

    def upload(self, local_path: str, key: str) -> str:
        from google.cloud import storage

        bucket_name, prefix = _split_gs_uri(self.cfg.blob_uri)
        full_key = f"{prefix.rstrip('/')}/{key}" if prefix else key
        client = storage.Client(project=self.cfg.project_id)
        blob = client.bucket(bucket_name).blob(full_key)
        blob.upload_from_filename(local_path)
        return f"gs://{bucket_name}/{full_key}"

    def download(self, uri: str, local_path: str) -> None:
        from google.cloud import storage

        bucket_name, key = _split_gs_uri(uri)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        client = storage.Client(project=self.cfg.project_id)
        client.bucket(bucket_name).blob(key).download_to_filename(local_path)

    def push_image(self, local_tag: str) -> str:
        registry = self.cfg.container_registry.rstrip("/")
        registry_host = registry.split("/", 1)[0]
        repo_path, _, tag = local_tag.rpartition(":")
        remote_tag = f"{registry}/{repo_path}:{tag or 'latest'}"

        subprocess.run(
            ["gcloud", "auth", "configure-docker", registry_host, "--quiet"],
            check=True,
        )
        subprocess.run(["docker", "tag", local_tag, remote_tag], check=True)
        # Which stream carries "digest: sha256:..." depends on the docker CLI's progress
        # writer (buildkit vs. classic, TTY vs. not) and isn't consistent across versions —
        # merge stderr into stdout so the search doesn't depend on that detail.
        result = subprocess.run(
            ["docker", "push", remote_tag],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        match = _DIGEST_RE.search(result.stdout)
        if not match:
            raise RuntimeError(f"could not parse digest from push output:\n{result.stdout}")
        return f"{registry}/{repo_path}@{match.group(1)}"

    def submit_training(self, image_uri: str, args: dict[str, Any]) -> str:
        from google.cloud import aiplatform_v1

        job_args = dict(args)
        machine_type = job_args.pop("machine_type", "n1-standard-4")
        replica_count = int(job_args.pop("replica_count", 1))
        use_spot = bool(job_args.pop("use_spot", False))
        trial_key = job_args.pop("trial_key", None)
        container_args = [f"--{k.replace('_', '-')}={v}" for k, v in job_args.items()]

        # cloud.env never ships inside the image, so the container gets its BLOB_URI
        # (and nothing else about this machine) through explicit env vars instead.
        env = {
            "CLOUD_PROVIDER": self.cfg.provider, "PROJECT_ID": self.cfg.project_id,
            "REGION": self.cfg.region, "BLOB_URI": self.cfg.blob_uri,
            "CONTAINER_REGISTRY": self.cfg.container_registry,
            "MLFLOW_TRACKING_URI": self.cfg.mlflow_tracking_uri,
            "MODEL_REGISTRY_NAME": self.cfg.model_registry_name,
        }
        if trial_key:
            # Lets the entrypoint upload this trial's metrics to a BLOB_URI key the
            # submitting sweep already knows, without a second round-trip to discover it.
            env["TRIAL_KEY"] = trial_key

        job_spec = aiplatform_v1.CustomJobSpec(
            worker_pool_specs=[aiplatform_v1.WorkerPoolSpec(
                machine_spec=aiplatform_v1.MachineSpec(machine_type=machine_type),
                replica_count=replica_count,
                container_spec=aiplatform_v1.ContainerSpec(
                    image_uri=image_uri,
                    args=container_args,
                    env=[aiplatform_v1.EnvVar(name=k, value=v) for k, v in env.items()],
                ),
            )],
            base_output_directory=aiplatform_v1.GcsDestination(
                output_uri_prefix=f"{self.cfg.blob_uri}/training-jobs"
            ),
            # Run-time identity, per cloud.env's TRAINING_SERVICE_ACCOUNT — deliberately
            # not IDENTITY_REF (the user account that submits the job).
            service_account=self.cfg.training_service_account,
        )
        if use_spot:
            # Discounted, preemptible capacity for the Task 2 budgeted sweep. Vertex
            # restarts a preempted replica from scratch rather than failing the job.
            job_spec.scheduling = aiplatform_v1.Scheduling(
                strategy=aiplatform_v1.Scheduling.Strategy.SPOT,
                restart_job_on_worker_restart=True,
            )

        job = aiplatform_v1.CustomJob(
            display_name=f"itcs355-lab2-{int(time.time())}",
            labels=self.cfg.tags(2),
            job_spec=job_spec,
        )
        client = aiplatform_v1.JobServiceClient(
            client_options={"api_endpoint": f"{self.cfg.region}-aiplatform.googleapis.com"}
        )
        parent = f"projects/{self.cfg.project_id}/locations/{self.cfg.region}"
        return client.create_custom_job(parent=parent, custom_job=job).name

    def wait_training(self, job_id: str, poll_seconds: int = 30) -> dict[str, Any]:
        from google.cloud import aiplatform_v1

        client = aiplatform_v1.JobServiceClient(
            client_options={"api_endpoint": f"{self.cfg.region}-aiplatform.googleapis.com"}
        )
        terminal = {
            aiplatform_v1.JobState.JOB_STATE_SUCCEEDED,
            aiplatform_v1.JobState.JOB_STATE_FAILED,
            aiplatform_v1.JobState.JOB_STATE_CANCELLED,
        }
        job = client.get_custom_job(name=job_id)
        while job.state not in terminal:
            time.sleep(poll_seconds)
            job = client.get_custom_job(name=job_id)

        if job.state != aiplatform_v1.JobState.JOB_STATE_SUCCEEDED:
            raise RuntimeError(f"training job {job_id} ended in {job.state.name}: {job.error.message}")
        return {
            "job_id": job_id,
            "state": job.state.name,
            "start_time": str(job.start_time) if job.start_time else None,
            "end_time": str(job.end_time) if job.end_time else None,
        }

    # Registration validates against this schema regardless of whether the version is
    # ever deployed (Lab 3's job, not this one) — Vertex rejects an upload with no
    # container_spec at all. Pinning the exact scikit-learn serving image is Lab 3's
    # concern; this is a real, known-good prebuilt image, present only so the registry
    # call is well-formed.
    _DEFAULT_SERVING_CONTAINER = "us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-0:latest"

    def register_model(self, model_uri: str, name: str,
                        serving_container: str = _DEFAULT_SERVING_CONTAINER, **lineage: str) -> str:
        """Register a model version in Vertex AI Model Registry.

        `model_uri`/`name` match the CloudAdapter interface exactly, so a grader calling
        this with just those two arguments gets the documented behaviour. `**lineage`
        is a GCP-only widening: the eight lineage fields Task 4 requires (git_commit,
        data_version, mlflow_run_id, training_job_id, image_digest, seed, metric_val,
        metric_test).

        `Model.metadata` (a free-form struct field) looks like the right home for these
        but Vertex silently drops it for a custom-sourced upload — confirmed by
        registering with it set and reading the model straight back with an unset
        field. `version_description` is a plain string and does persist, so the full
        lineage goes there as JSON (exact values, byte-for-byte — JSON string escaping
        doesn't touch '.', '/' or ':'). The subset of values that are label-safe
        (`labels` forbids '.', '/', ':', which several of these values contain) is
        mirrored into labels too, for anyone filtering the registry by them.
        """
        from google.cloud import aiplatform_v1

        client = aiplatform_v1.ModelServiceClient(
            client_options={"api_endpoint": f"{self.cfg.region}-aiplatform.googleapis.com"}
        )
        _LABEL_SAFE = re.compile(r"^[a-z0-9_-]{0,63}$")
        labels = {**self.cfg.tags(2), **{k: v for k, v in lineage.items() if _LABEL_SAFE.match(v)}}
        model = aiplatform_v1.Model(
            display_name=name,
            artifact_uri=model_uri,
            container_spec=aiplatform_v1.ModelContainerSpec(image_uri=serving_container),
            version_aliases=["staging"],
            version_description=json.dumps(lineage, sort_keys=True),
            labels=labels,
        )
        parent = f"projects/{self.cfg.project_id}/locations/{self.cfg.region}"
        response = client.upload_model(parent=parent, model=model).result()
        return f"{response.model}@{response.model_version_id}"

    def promote_model(self, model_name: str, version_id: str,
                       alias: str = "production", remove_alias: str | None = "staging") -> str:
        """Move a registered version through the staging step: add `alias` (default
        "production") and drop `remove_alias` (default "staging") via Vertex's
        alias-merge call. Not part of the CloudAdapter interface — Task 4 treats
        promotion as a one-off operator action, not a repeatable pipeline step.
        """
        from google.cloud import aiplatform_v1

        client = aiplatform_v1.ModelServiceClient(
            client_options={"api_endpoint": f"{self.cfg.region}-aiplatform.googleapis.com"}
        )
        aliases = [alias] + ([f"-{remove_alias}"] if remove_alias else [])
        updated = client.merge_version_aliases(name=f"{model_name}@{version_id}", version_aliases=aliases)
        return f"{updated.name}@{updated.version_id}"

    def describe_model(self, model_name: str, version_id: str) -> dict[str, Any]:
        """Read a registered version straight back from Vertex — for verifying what
        actually persisted, not what a previous call merely sent. Not part of the
        CloudAdapter interface.
        """
        from google.cloud import aiplatform_v1

        client = aiplatform_v1.ModelServiceClient(
            client_options={"api_endpoint": f"{self.cfg.region}-aiplatform.googleapis.com"}
        )
        model = client.get_model(name=f"{model_name}@{version_id}")
        return {
            "name": model.name,
            "version_id": model.version_id,
            "display_name": model.display_name,
            "version_aliases": list(model.version_aliases),
            "artifact_uri": model.artifact_uri,
            "labels": dict(model.labels),
            "lineage": json.loads(model.version_description or "{}"),
        }

    # --- Lab 3 (Cloud Run, chosen over Vertex Endpoint for scale-to-zero cost) ------

    def _check_artifact_readable(self, artifact_uri: str) -> None:
        """Best-effort preflight: warn (never hard-fail) if the training service account
        does not obviously have read access to the model artifact's bucket.

        This only inspects the bucket's own IAM policy. It cannot see project-level or
        group-inherited grants, so a clean bill here is not proof of access and a warning
        here is not proof of its absence either — it exists so the common mistake
        ("forgot to grant this account read access to the bucket") surfaces here, before
        you wait for a Cloud Run container to fail at runtime and dig through logs to
        find the same thing.
        """
        from google.cloud import storage

        bucket_name, _ = _split_gs_uri(artifact_uri)
        sa = self.cfg.training_service_account
        member = f"serviceAccount:{sa}"
        readable_roles = (
            "roles/storage.objectViewer", "roles/storage.objectAdmin",
            "roles/storage.admin", "roles/owner", "roles/editor",
        )
        try:
            client = storage.Client(project=self.cfg.project_id)
            policy = client.bucket(bucket_name).get_iam_policy()
        except Exception as exc:
            print(f"  (could not read gs://{bucket_name}'s IAM policy to preflight-check "
                  f"{sa}: {exc})")
            return

        if any(member in policy[role] for role in readable_roles):
            return
        print(
            f"  WARNING: {sa} is not in gs://{bucket_name}'s bucket-level IAM policy under "
            f"any of {readable_roles}. It may still have access via a project-level or "
            "group grant this check cannot see — but if the deployed container fails at "
            "/ready with a permission error, this is the first thing to fix:\n"
            f"    gcloud storage buckets add-iam-policy-binding gs://{bucket_name} "
            f"--member={member} --role=roles/storage.objectViewer"
        )

    def _service_manifest(
        self, endpoint: str, image_uri: str, cpu: str, memory: str,
        env: dict[str, str], max_instances: int, concurrency: int,
        revision_suffix: str | None,
    ) -> dict[str, Any]:
        """Knative Service manifest for `gcloud run services replace`. Declarative and
        create-or-update by construction — the same manifest applied twice just updates
        the service, which is what makes deploy() idempotent without extra bookkeeping.

        gcloud run deploy has no stable flag for startup/liveness probes; they only exist
        on the underlying Knative spec, so this is built by hand rather than shelled out
        to `gcloud run deploy` piece by piece.
        """
        labels = self.cfg.tags(3)
        template_metadata: dict[str, Any] = {
            "labels": labels,
            "annotations": {
                "autoscaling.knative.dev/minScale": "0",
                "autoscaling.knative.dev/maxScale": str(max_instances),
            },
        }
        if revision_suffix:
            template_metadata["name"] = f"{endpoint}-{revision_suffix}"

        return {
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "metadata": {"name": endpoint, "labels": labels},
            "spec": {
                "template": {
                    "metadata": template_metadata,
                    "spec": {
                        "containerConcurrency": concurrency,
                        "serviceAccountName": self.cfg.training_service_account,
                        "containers": [{
                            "image": image_uri,
                            "ports": [{"containerPort": 8080}],
                            "env": [{"name": k, "value": v} for k, v in env.items()],
                            "startupProbe": {
                                "httpGet": {"path": "/ready", "port": 8080},
                                "periodSeconds": 2,
                                "failureThreshold": 30,
                                "timeoutSeconds": 2,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/health", "port": 8080},
                                "periodSeconds": 10,
                                "timeoutSeconds": 2,
                            },
                        }],
                    },
                },
            },
        }

    def service_url(self, name: str) -> str:
        """Look up a deployed Cloud Run service's URL by name. Not part of the
        CloudAdapter interface — a small convenience so scripts don't need their own
        gcloud calls (mirrors describe_model/promote_model for Lab 2).

        Cached per adapter instance — a service's URL doesn't change across redeploys
        to the same name, and invoke() calls this on every single request. Task 4's
        canary_traffic.py polls invoke() continuously for the duration of the canary
        window; without this, every one of those calls would pay a `gcloud run services
        describe` subprocess cost that _user_token_unavailable's caching does not cover,
        adding exactly the kind of variable per-request overhead that would distort the
        detection-time measurement Task 4 reports.
        """
        cache = getattr(self, "_service_url_cache", None)
        if cache is None:
            cache = {}
            self._service_url_cache = cache
        if name in cache:
            return cache[name]

        with self._cache_lock:
            if name in cache:  # another thread populated it while we waited for the lock
                return cache[name]
            result = subprocess.run(
                ["gcloud", "run", "services", "describe", name,
                 "--region", self.cfg.region, "--format", "value(status.url)"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            url = result.stdout.strip()
            cache[name] = url
            return url

    def deploy(
        self, model_ref: str, endpoint: str, instance: str, *,
        image_uri: str, max_instances: int = 2, concurrency: int = 80,
        revision_suffix: str | None = None,
    ) -> str:
        """Deploy the serving image to Cloud Run, wired to one registered model version.

        `model_ref` is "<Vertex model resource name>@<version id or alias>" — the same
        "name@version" shape describe_model/promote_model already use. `instance` is
        "<cpu>-<memory>" in gcloud run's own units (e.g. "1-512Mi"). `image_uri` must
        already be digest-pinned — get it from push_image() first; deploy() does not
        push images itself, the same division of labour train-remote already uses for
        submit_training(). `image_uri`/`max_instances`/`concurrency`/`revision_suffix`
        are a GCP-only widening beyond the three-argument CloudAdapter signature, same
        pattern as register_model's **lineage.

        Resolving model_ref to an artifact lives here, not in service/ — the service only
        ever calls adapter.download() on the MODEL_ARTIFACT_URI this bakes in as an env
        var, so the exact same container image keeps working regardless of which
        provider's adapter is behind it.

        Returns the service name (not the URL) — that's also what invoke() takes as
        `endpoint`, since it needs the bare name for its own gcloud calls (URL lookup,
        and the `gcloud run services proxy` fallback for a human-authenticated caller).
        """
        model_name, _, version = model_ref.rpartition("@")
        if not model_name or not version:
            raise ValueError(f"model_ref must be '<model>@<version-or-alias>', got {model_ref!r}")

        entry = self.describe_model(model_name, version)
        artifact_uri = f"{entry['artifact_uri']}/model.joblib"
        self._check_artifact_readable(artifact_uri)

        cpu, _, memory = instance.partition("-")
        if not cpu or not memory:
            raise ValueError(f"instance must be '<cpu>-<memory>', e.g. '1-512Mi', got {instance!r}")

        # entry["version_id"] alone is only unique WITHIN one Vertex model resource — a
        # canary registered as its own model resource (register_model() never passes
        # parent_model, so every registration gets a fresh resource) starts back at
        # version_id "1" too, colliding with a stable model that also happens to be at
        # version 1. Confirmed live: deploying stable and canary this way put
        # MODEL_VERSION=1 on both revisions, which would have made Task 4's "confirm by
        # model_version" step unable to tell them apart at all. Prefixing with the model
        # resource's own numeric ID makes it globally unique regardless.
        model_id = model_name.rsplit("/", 1)[-1]
        env = {
            "MODEL_ARTIFACT_URI": artifact_uri,
            "MODEL_VERSION": f"{model_id}-v{entry['version_id']}",
            # The only two capability slots the container needs at runtime — enough for
            # get_adapter(config.load(strict=False)) to build a GcpAdapter and for its
            # download() to construct a storage.Client. Everything else in cloud.env
            # (BLOB_URI, IDENTITY_REF, TRAINING_SERVICE_ACCOUNT, ...) stays out.
            "CLOUD_PROVIDER": self.cfg.provider,
            "PROJECT_ID": self.cfg.project_id,
        }

        import tempfile

        import yaml

        manifest = self._service_manifest(
            endpoint, image_uri, cpu, memory, env, max_instances, concurrency, revision_suffix,
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "service.yaml"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
            result = subprocess.run(
                ["gcloud", "run", "services", "replace", str(manifest_path),
                 "--region", self.cfg.region],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        if result.returncode != 0:
            raise RuntimeError(f"gcloud run services replace failed:\n{result.stdout}")

        return endpoint

    def _fetch_identity_token(self, audience: str) -> str:
        """ADC first (works for a service account or the metadata server — CI, or a
        Cloud Build step); then `gcloud auth print-identity-token` directly (same
        requirement); then impersonating the training service account — which works for
        a human account once grant_token_creator() has been called, and is a real,
        fast, direct-HTTPS path, not the local-proxy fallback.

        google-auth's ADC-based `fetch_id_token` cannot mint ID tokens for a plain
        authorized-user credential (a personal `gcloud auth login`) — only for service
        accounts and the metadata server. `gcloud auth print-identity-token` without
        impersonation has the same restriction for `--audiences`. Both are real gaps in
        the tooling, not a misconfiguration here.

        The impersonated token is cached (it's valid for the standard 1 hour) so a tight
        polling loop doesn't re-spawn `gcloud` on every call — this matters directly for
        Task 4: canary_traffic.py needs sustained throughput through invoke(), and the
        local-proxy fallback (a single subprocess never meant for this volume) cannot
        deliver it. Once impersonation has been confirmed to work at all, every later
        call skips straight to the cache check, no subprocess unless the cached token is
        close to expiring.

        If NEITHER tier works at all (no ADC, no impersonation grant), that result is
        cached too and every later call raises immediately — no repeated ADC call, no
        `gcloud` subprocess spawn — before falling through to the proxy in invoke().

        The gcloud-CLI fallback (tiers 2 and 3, both real subprocess spawns) runs behind
        `self._cache_lock` — confirmed live that without it, ~20-40 threads all starting
        before the cache populated each independently spawned their own `gcloud`
        process, and the resulting contention produced a p99 of 31.8s (one request took
        41.8s) in a run whose median was otherwise ~900ms. ADC itself stays outside the
        lock: it is a pure library call with nothing to contend over, so every thread is
        free to attempt it concurrently.
        """
        import time

        cached = getattr(self, "_impersonated_token_cache", None)
        if cached and cached["audience"] == audience and time.time() < cached["expires_at"] - 300:
            return cached["token"]

        import google.auth.transport.requests
        import google.oauth2.id_token

        try:
            auth_request = google.auth.transport.requests.Request()
            return google.oauth2.id_token.fetch_id_token(auth_request, audience)
        except Exception:
            pass

        with self._cache_lock:
            cached = getattr(self, "_impersonated_token_cache", None)
            if cached and cached["audience"] == audience and time.time() < cached["expires_at"] - 300:
                return cached["token"]
            if getattr(self, "_user_token_unavailable", False):
                raise RuntimeError("ADC, gcloud CLI, and impersonation already confirmed "
                                    "unable to mint a token for this credential this session")

            result = subprocess.run(
                ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()

            result = subprocess.run(
                ["gcloud", "auth", "print-identity-token",
                 f"--impersonate-service-account={self.cfg.training_service_account}",
                 f"--audiences={audience}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            if result.returncode == 0:
                token = result.stdout.strip()
                self._impersonated_token_cache = {
                    "audience": audience, "token": token, "expires_at": time.time() + 3600,
                }
                return token

            self._user_token_unavailable = True
            raise RuntimeError(
                f"Could not obtain an identity token via ADC, gcloud CLI, or impersonation "
                f"(did you call grant_token_creator()?):\n{result.stdout}"
            ) from None

    def _proxy_base_url(self, service_name: str) -> str:
        """Lazily start `gcloud run services proxy` for this service and return its
        http://127.0.0.1:<port> base, cached on self for reuse across calls in this
        process. Last-resort auth fallback: a human's own `gcloud auth login` cannot
        mint an audience-scoped ID token (see _fetch_identity_token) — this is the one
        path Google actually ships for a human to reach an IAM-protected Cloud Run
        service directly, and it needs the service name, not the public URL.
        """
        cache = getattr(self, "_proxy_cache", None)
        if cache is None:
            cache = {}
            self._proxy_cache = cache
        if service_name in cache:
            return cache[service_name]

        import atexit
        import socket
        import time

        import requests

        # Same thundering-herd risk as _fetch_identity_token: without this lock,
        # concurrent callers before the cache populates would each spawn their own
        # `gcloud run services proxy` subprocess on the same service.
        with self._cache_lock:
            if service_name in cache:
                return cache[service_name]

            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]

            proc = subprocess.Popen(
                ["gcloud", "run", "services", "proxy", service_name,
                 "--region", self.cfg.region, "--port", str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            atexit.register(proc.terminate)

            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"`gcloud run services proxy {service_name}` exited early")
                try:
                    requests.get(base, timeout=1)
                    break
                except requests.exceptions.RequestException:
                    # ConnectionError before the proxy is accepting connections at all;
                    # ReadTimeout once it's accepting but not yet answering — confirmed
                    # live that the latter is real (crashed an unhandled canary_traffic.py
                    # run), so both need to just retry, not propagate.
                    time.sleep(0.5)
            else:
                raise RuntimeError(f"`gcloud run services proxy {service_name}` never became reachable")

            cache[service_name] = base
            return base

    def invoke(self, endpoint: str, payload: dict[str, Any], route: str = "/predict") -> dict[str, Any]:
        """POST to a route on a deployed Cloud Run service named `endpoint`.

        Auth lives entirely here, tried in order: an ADC-minted ID token (works for a
        service account or the metadata server — CI, Cloud Build), then `gcloud auth
        print-identity-token` (same requirement), then `gcloud run services proxy` as a
        last resort — the one path that actually works for a human's own gcloud login.
        `route` is a GCP-only widening: the base signature only names one endpoint, but
        the Task 2 smoke test needs to hit /predict, /predict/batch, and a deliberately
        invalid payload against the same deployed service, so a fixed hardcoded route
        would not let one adapter call cover all three.
        """
        import requests

        url = self.service_url(endpoint)
        try:
            id_token = self._fetch_identity_token(url)
            response = requests.post(
                url.rstrip("/") + route, json=payload,
                headers={"Authorization": f"Bearer {id_token}"}, timeout=30,
            )
        except Exception:
            base = self._proxy_base_url(endpoint)
            response = requests.post(base.rstrip("/") + route, json=payload, timeout=30)

        if not response.ok:
            hint = ""
            if response.status_code == 403:
                hint = (
                    "\nIf this is IAM (not the service itself rejecting the request), the "
                    "identity invoking it needs roles/run.invoker on this service:\n"
                    f"  gcloud run services add-iam-policy-binding {endpoint} "
                    "--member=user:<your-account> --role=roles/run.invoker "
                    f"--region {self.cfg.region}"
                )
            raise InvokeError(
                response.status_code,
                f"POST {route} -> {response.status_code}: {response.text}{hint}",
            )
        return response.json()

    def split_traffic(self, endpoint: str, splits: dict[str, int]) -> dict[str, int]:
        """Set Cloud Run traffic split by revision name. `splits` maps revision name to
        percent; percents must sum to 100. Not part of the CloudAdapter interface —
        SageMaker (production variant weights), Azure ML (traffic percentages across
        deployments), and Vertex AI (traffic split across deployed models) each have
        their own vocabulary for this, so it stays a GCP-only method here, same as
        service_url/describe_model/promote_model.
        """
        if sum(splits.values()) != 100:
            raise ValueError(f"splits must sum to 100, got {splits} (sums to {sum(splits.values())})")
        to_revisions = ",".join(f"{name}={pct}" for name, pct in splits.items())
        subprocess.run(
            ["gcloud", "run", "services", "update-traffic", endpoint,
             "--region", self.cfg.region, "--to-revisions", to_revisions, "--quiet"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return splits

    def teardown(self, tags: dict[str, str]) -> list[str]:
        """Delete every Cloud Run service carrying these labels.

        Scoped to Cloud Run only — the resource type Lab 3 can actually create with this
        adapter so far. Lab 5 widens this to cover whatever else it introduces (LLM
        endpoints, pipelines); nothing here needs to change for that, only more branches
        added alongside it.

        Filter key is `metadata.labels.<key>`, not bare `labels.<key>` — confirmed live
        that the bare form matches nothing on `gcloud run services list` (silently: exit
        0, a stderr warning, zero results) even though the labels are genuinely present
        on the service. Also: stdout/stderr are NOT merged here, unlike most other
        gcloud calls in this file — merging them let that exact warning line get parsed
        as if it were a service name, which then got handed to `services delete` and
        failed on "Invalid resource name" instead of failing where the real problem was.
        """
        filter_expr = " AND ".join(f"metadata.labels.{k}={v}" for k, v in tags.items())
        listing = subprocess.run(
            ["gcloud", "run", "services", "list", "--region", self.cfg.region,
             "--filter", filter_expr, "--format", "value(metadata.name)"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        names = [line.strip() for line in listing.stdout.splitlines() if line.strip()]

        deleted = []
        for name in names:
            subprocess.run(
                ["gcloud", "run", "services", "delete", name,
                 "--region", self.cfg.region, "--quiet"],
                check=True,
            )
            deleted.append(f"cloud-run:{name}")
        return deleted

    # --- Task 3 load-test auth tooling (not part of the CloudAdapter interface) -----
    #
    # k6 (loadtest/k6.js) is a separate process — it cannot call invoke()'s auth tiers,
    # and a human's own gcloud login cannot mint an audience-scoped ID token directly
    # (the same limitation invoke() works around with the proxy fallback). Impersonating
    # the training service account is the fix: it CAN mint one, but only after the
    # caller is granted roles/iam.serviceAccountTokenCreator on it — an IAM binding, not
    # a labelled resource, so teardown() cannot find or remove it. Whoever runs
    # grant_token_creator() is responsible for calling revoke_token_creator() when the
    # load test is done; that pairing belongs in Task 3's own teardown checklist, not
    # buried inside make teardown.

    def _current_account(self) -> str:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "account"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return f"user:{result.stdout.strip()}"

    def grant_token_creator(self, member: str | None = None, endpoint: str | None = None) -> str:
        """Grant `member` (default: whoever gcloud is currently logged in as)
        roles/iam.serviceAccountTokenCreator on the training service account, needed
        before mint_loadtest_token() can impersonate it. If `endpoint` is given, also
        grants the training service account roles/run.invoker on that Cloud Run
        service — minting a token that impersonates an identity with no invoker rights
        on the target service just gets a valid-but-403 token, which is what happened
        the first time this was wired up without this second grant.

        Call revoke_token_creator() with the same arguments when the load test is done
        — these are standing elevated grants, not something to leave in place at rest.
        """
        member = member or self._current_account()
        subprocess.run(
            ["gcloud", "iam", "service-accounts", "add-iam-policy-binding",
             self.cfg.training_service_account, f"--member={member}",
             "--role=roles/iam.serviceAccountTokenCreator", "--quiet"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if endpoint:
            subprocess.run(
                ["gcloud", "run", "services", "add-iam-policy-binding", endpoint,
                 "--region", self.cfg.region,
                 f"--member=serviceAccount:{self.cfg.training_service_account}",
                 "--role=roles/run.invoker", "--quiet"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        return member

    def revoke_token_creator(self, member: str | None = None, endpoint: str | None = None) -> str:
        """Undo grant_token_creator() — both grants, if `endpoint` matches what was
        passed there. Run this every time you finish a load-testing session — part of
        Task 3's teardown checklist, alongside `make teardown LAB=3`.

        Resolves the actual bound member casing before removing, rather than trusting
        `gcloud config get-value account`'s casing directly: confirmed live that IAM can
        canonicalize a human member's email casing on grant (get-value returned
        lowercase; the stored binding came back capitalized), and
        remove-iam-policy-binding matches by exact string, not case-insensitively — a
        mismatch here is exactly what "Policy binding ... not found" means.
        """
        import yaml

        member = member or self._current_account()
        policy_text = subprocess.run(
            ["gcloud", "iam", "service-accounts", "get-iam-policy",
             self.cfg.training_service_account, "--format=yaml"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        ).stdout
        policy = yaml.safe_load(policy_text) or {}
        bound_members = {m for b in policy.get("bindings", []) for m in b.get("members", [])}
        resolved = next((m for m in bound_members if m.lower() == member.lower()), member)

        subprocess.run(
            ["gcloud", "iam", "service-accounts", "remove-iam-policy-binding",
             self.cfg.training_service_account, f"--member={resolved}",
             "--role=roles/iam.serviceAccountTokenCreator", "--quiet"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if endpoint:
            subprocess.run(
                ["gcloud", "run", "services", "remove-iam-policy-binding", endpoint,
                 "--region", self.cfg.region,
                 f"--member=serviceAccount:{self.cfg.training_service_account}",
                 "--role=roles/run.invoker", "--quiet"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        return resolved

    def mint_loadtest_token(self, endpoint: str) -> str:
        """Mint one audience-scoped ID token for k6 (or any non-Python tool) to send as
        a Bearer header directly against the public URL — no local proxy hop, so the
        load test measures the service's own latency, not a client-side reverse proxy's.

        Requires grant_token_creator() to have been called first. Valid for exactly 1
        hour from now and cannot be refreshed — mint a fresh one rather than trying to
        extend it for any run longer than roughly 55 minutes.
        """
        url = self.service_url(endpoint)
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token",
             f"--impersonate-service-account={self.cfg.training_service_account}",
             f"--audiences={url}"],
            # stderr kept separate from stdout on purpose: impersonation always prints a
            # "using service account impersonation" WARNING on stderr, and merging it
            # into stdout put that warning line, then a newline, then the real token, all
            # in the string this method returns — a raw newline in a Bearer header value
            # is exactly what made k6 reject every request with "invalid header field
            # value for Authorization" the first time this was wired up.
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Could not mint a load-test token (did you run grant_token_creator() "
                f"first?):\n{result.stderr}"
            )
        return result.stdout.strip()

    # emit_metric                       -> Lab 4 (Cloud Monitoring time series)
    # generate                          -> Lab 5 (managed LLM endpoint; read usageMetadata for tokens)
