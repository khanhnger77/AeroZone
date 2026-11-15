"""Deploy the MARS ODYSSEY backend to Google Cloud Run.

Cloud Build builds the repository's Dockerfile from this folder and Cloud Run serves the
planner API (/api), the Copilot and the sim server (/sim), plus the web app, at a permanent
https://<service>-<hash>.<region>.run.app address. GitHub Pages hosts the static frontend and
calls that address (docs/deployment.md).

What is uploaded: this folder minus .gcloudignore, so the git-ignored terrain rasters in
data/processed and data/analysis (about 50 MB) go with the code and the container never downloads
the 1.8 GB DTM. Never uploaded: api_key.credentials. With --set-key the key becomes the service's
OPENROUTER_API_KEY environment variable instead.

Service settings, chosen for a free-tier demo:
  * 2 GiB memory: measured ~360 MB idle and ~1.1 GB with the world demo running;
  * min 0 instances, so nothing is billed while nobody uses it (the first visit waits ~10-20 s);
  * max 1 instance: the sim keeps one shared world, and it caps the cost;
  * 3600 s request timeout, the longest a sim WebSocket can stay open before it reconnects.

Usage (needs the Google Cloud CLI, `gcloud auth login`, and a project with billing enabled):
    pixi run deploy-cloudrun --project <project-id> [--region us-central1] [--set-key] [--cors-origin URL]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "api_key.credentials"
RASTERS = (ROOT / "data" / "processed" / "elevation_5m.tif", ROOT / "data" / "analysis" / "hazard_5m.tif")
APIS = ("run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com")
INSTALL = "https://cloud.google.com/sdk/docs/install (Windows: winget install Google.CloudSDK)"


def gcloud() -> str:
    exe = shutil.which("gcloud")
    if not exe:
        sys.exit(f"gcloud not found. Install the Google Cloud CLI: {INSTALL}, then run `gcloud auth login`.")
    return exe


def run(cmd: list[str], *, capture: bool = False) -> str:
    shown = [c if "OPENROUTER_API_KEY" not in c else "--update-env-vars=<key hidden>" for c in cmd[1:]]
    print("$ gcloud " + " ".join(shown), flush=True)
    done = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture)
    if done.returncode != 0:
        if capture:
            print(done.stderr, file=sys.stderr)
        sys.exit(f"gcloud failed (exit {done.returncode})")
    return done.stdout.strip() if capture else ""


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
    ap.add_argument("--project", required=True, help="Google Cloud project ID (with billing enabled)")
    ap.add_argument("--region", default="us-central1", help="Cloud Run region (default: us-central1)")
    ap.add_argument("--service", default="aerozone", help="Cloud Run service name (default: aerozone)")
    ap.add_argument("--set-key", action="store_true", help=f"set OPENROUTER_API_KEY from {KEY_FILE.name}")
    ap.add_argument("--cors-origin", help="restrict /sim to this origin, e.g. https://<owner>.github.io (default: any)")
    args = ap.parse_args(argv)

    missing = [p.relative_to(ROOT).as_posix() for p in RASTERS if not p.is_file()]
    if missing:
        sys.exit(f"{', '.join(missing)} missing: run `pixi run data` first")
    if not (ROOT / ".gcloudignore").is_file():
        sys.exit(".gcloudignore missing: without it gcloud follows .gitignore and drops the terrain rasters")

    exe = gcloud()
    common = ["--project", args.project, "--quiet"]
    run([exe, "services", "enable", *APIS, *common])

    env: dict[str, str] = {}
    if args.set_key:
        env["OPENROUTER_API_KEY"] = read_key()
    if args.cors_origin:
        env["MO_CORS_ORIGINS"] = args.cors_origin.rstrip("/")
    deploy = [exe, "run", "deploy", args.service, "--source", ".", "--region", args.region,
              "--allow-unauthenticated", "--port", "7860", "--memory", "2Gi", "--cpu", "1",
              "--min-instances", "0", "--max-instances", "1", "--timeout", "3600", "--cpu-boost", *common]
    if env:   # "^|^" makes | the separator, so values may contain commas
        deploy.append("--update-env-vars=^|^" + "|".join(f"{k}={v}" for k, v in env.items()))
    run(deploy)

    url = run([exe, "run", "services", "describe", args.service, "--region", args.region,
               "--format", "value(status.url)", "--project", args.project], capture=True)
    print(f"\nBackend live at {url}\n  check {url}/api/health\n  set the GitHub variable BACKEND_URL = {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
