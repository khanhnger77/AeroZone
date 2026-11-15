"""Publish the MARS ODYSSEY backend to a Hugging Face Docker Space.

The Space builds the repository's Dockerfile and serves the planner API (/api), the Copilot and
the sim server (/sim), plus the web app, at https://<user>-<space>.hf.space. GitHub Pages hosts
the static frontend and calls that address (docs/deployment.md).

What is uploaded:
  * every git-tracked file, so the Space matches the checked-out commit;
  * the git-ignored terrain rasters in data/processed and data/analysis (about 50 MB), so the
    container does not have to download the 1.8 GB DTM and rebuild them on start-up;
  * a Space README holding the configuration header Hugging Face requires (sdk: docker, port).
Files that are in the Space but no longer in this set are deleted. With --code-only the rasters
are neither uploaded nor deleted (the GitHub Action, which has no data, uses this).

Never uploaded: api_key.credentials. --set-key stores its key as an encrypted Space secret.

Usage (token from `HF_TOKEN` or a prior `huggingface-cli login`; needs write access):
    pixi run deploy-space --space <user>/<space> [--code-only] [--set-key] [--cors-origin URL]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = ("data/processed", "data/analysis")
SKIP = ("media/",)                       # tracked but not used by the app (presentation video)
KEY_FILE = ROOT / "api_key.credentials"

SPACE_HEADER = """---
title: MARS ODYSSEY
emoji: 🪐
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Jezero Crater route planner, 3D rover sim and Copilot
---

"""


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    files = [f for f in out.decode("utf-8").split("\0") if f]
    return [f for f in files if not f.startswith(SKIP) and f != "README.md" and (ROOT / f).is_file()]


def data_files() -> list[str]:
    files = [p.relative_to(ROOT).as_posix() for d in DATA_DIRS for p in sorted((ROOT / d).rglob("*")) if p.is_file()]
    if not any(f.endswith("elevation_5m.tif") for f in files) or not any(f.endswith("hazard_5m.tif") for f in files):
        sys.exit("data/processed/elevation_5m.tif or data/analysis/hazard_5m.tif missing: run `pixi run data` first")
    return files


def read_key() -> str:
    if not KEY_FILE.is_file():
        sys.exit(f"--set-key: {KEY_FILE.name} not found (see README, Odyssey Copilot)")
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line.split("=", 1)[1].strip() if line.startswith("OPENROUTER_API_KEY=") else line
    sys.exit(f"--set-key: {KEY_FILE.name} holds no key")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--space", default=os.environ.get("HF_SPACE"), help="<user>/<space> (or set HF_SPACE)")
    ap.add_argument("--code-only", action="store_true", help="upload tracked files only; leave the Space's data alone")
    ap.add_argument("--set-key", action="store_true", help=f"store the key in {KEY_FILE.name} as the Space secret OPENROUTER_API_KEY")
    ap.add_argument("--cors-origin", help="restrict /sim to this origin, e.g. https://<owner>.github.io (default: any)")
    args = ap.parse_args(argv)
    if not args.space or "/" not in args.space:
        ap.error("--space <user>/<space> is required")

    api = HfApi(token=os.environ.get("HF_TOKEN") or None)
    api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)

    files = tracked_files() + ([] if args.code_only else data_files())
    readme = SPACE_HEADER + (ROOT / "README.md").read_text(encoding="utf-8")
    ops: list[CommitOperationAdd | CommitOperationDelete] = [CommitOperationAdd("README.md", readme.encode("utf-8"))]
    ops += [CommitOperationAdd(f, str(ROOT / f)) for f in files]

    keep = set(files) | {"README.md", ".gitattributes"}
    for f in api.list_repo_files(args.space, repo_type="space"):
        if f not in keep and not (args.code_only and f.startswith(tuple(d + "/" for d in DATA_DIRS))):
            ops.append(CommitOperationDelete(f))

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    print(f"uploading {len(files) + 1} files to spaces/{args.space} (commit {head or '?'})", flush=True)
    api.create_commit(args.space, repo_type="space", operations=ops,
                      commit_message=f"Deploy {head or 'working tree'}" + (" (code only)" if args.code_only else ""))

    if args.set_key:
        api.add_space_secret(args.space, "OPENROUTER_API_KEY", read_key())
        print("Space secret OPENROUTER_API_KEY set", flush=True)
    if args.cors_origin:
        api.add_space_variable(args.space, "MO_CORS_ORIGINS", args.cors_origin.rstrip("/"))
        print(f"Space variable MO_CORS_ORIGINS = {args.cors_origin}", flush=True)

    user, name = args.space.split("/", 1)
    host = f"{user}-{name}".lower().replace("_", "-").replace(".", "-")
    print(f"done: https://huggingface.co/spaces/{args.space}  ->  https://{host}.hf.space (builds for a few minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
