"""Lab 3 Task 2 — push the serving image and deploy it to Cloud Run.

Creates a BILLED resource the moment it succeeds. Requires --yes after you've reviewed
the printed plan and cost estimate — this is a second, durable gate beyond a one-off
chat confirmation, so a re-run months from now still has to look at the same numbers
before it can create anything.

Run `make teardown LAB=3` the moment you're done with it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config

DEFAULT_MODEL = "projects/126202218664/locations/asia-southeast1/models/437231793901404160"
DEFAULT_VERSION = "production"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Vertex Model resource name")
    ap.add_argument("--version", default=DEFAULT_VERSION, help="version id or alias")
    ap.add_argument("--endpoint", default="itcs355-predict", help="Cloud Run service name")
    ap.add_argument("--image", required=True, help="local docker tag to push, e.g. itcs355-serve:abc123")
    ap.add_argument("--instance", default="1-512Mi", help="'<cpu>-<memory>' in gcloud run units")
    ap.add_argument("--max-instances", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=80)
    ap.add_argument("--revision-suffix", default=None)
    ap.add_argument("--yes", action="store_true", help="actually create/update the resource")
    args = ap.parse_args()

    cfg = config.load()
    adapter = get_adapter(cfg)

    print("This will create or update a Cloud Run service:")
    print(f"  name:            {args.endpoint}")
    print(f"  region:          {cfg.region}")
    print(f"  model:           {args.model}@{args.version}")
    print(f"  local image:     {args.image}  (will be pushed and deployed by digest)")
    print(f"  instance:        {args.instance}  (cpu-memory)")
    print(f"  scaling:         min=0 (scale-to-zero), max={args.max_instances}, "
          f"concurrency={args.concurrency}")
    print(f"  service account: {cfg.training_service_account}")
    print(f"  labels:          {cfg.tags(3)}")
    print("  estimated cost:  ~0 THB/hr while idle (scale-to-zero); ~4.2 THB/hr ONLY "
          "while actively serving nonstop at this instance size (est., verify with the "
          "GCP pricing calculator)")
    print("  billed from the moment it exists — run `make teardown LAB=3` when done\n")

    if not args.yes:
        print("Re-run with --yes to actually deploy.")
        return 1

    image_uri = adapter.push_image(args.image)
    print(f"pushed image: {image_uri}")

    name = adapter.deploy(
        f"{args.model}@{args.version}", args.endpoint, args.instance,
        image_uri=image_uri, max_instances=args.max_instances,
        concurrency=args.concurrency, revision_suffix=args.revision_suffix,
    )
    print(f"deployed: {name} -> {adapter.service_url(name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
