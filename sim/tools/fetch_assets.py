"""Download the sim page's third-party assets: three.js r170 (vendored) and NASA's Perseverance model.

Why this exists: the page must work with no CDN at runtime (principle 9), and the rover glb is too
large to want in every clone. This script is idempotent: files already present are kept unless
`--force` is given. Provenance (url, bytes, sha256, credit, licence, time) goes to
`sim/assets_sources.json`.

Run: `python -m sim.tools.fetch_assets [--force]`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
THREE_VERSION = "0.170.0"
THREE_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"
VENDOR = SIM_DIR / "web" / "vendor" / "three"
USER_AGENT = "mars-odyssey-sim-fetch/1.0"

THREE_CREDIT = {"credit": "three.js authors", "licence": "MIT"}
DRACO_CREDIT = {"credit": "Google Draco authors (bundled with three.js)", "licence": "Apache-2.0"}
ROVER_CREDIT = {
    "credit": "NASA/JPL-Caltech, NASA 3D Resources: Mars 2020 Perseverance Rover",
    "licence": "NASA media usage guidelines (not subject to copyright in the US; no endorsement implied)",
}

ASSETS: list[tuple[str, Path, dict]] = [
    (f"{THREE_CDN}/build/three.module.js", VENDOR / "three.module.js", THREE_CREDIT),
    (f"{THREE_CDN}/examples/jsm/loaders/GLTFLoader.js", VENDOR / "addons/loaders/GLTFLoader.js",
     THREE_CREDIT),
    (f"{THREE_CDN}/examples/jsm/loaders/DRACOLoader.js", VENDOR / "addons/loaders/DRACOLoader.js",
     THREE_CREDIT),
    (f"{THREE_CDN}/examples/jsm/controls/OrbitControls.js",
     VENDOR / "addons/controls/OrbitControls.js", THREE_CREDIT),
    (f"{THREE_CDN}/examples/jsm/utils/BufferGeometryUtils.js",
     VENDOR / "addons/utils/BufferGeometryUtils.js", THREE_CREDIT),
] + [
    (f"{THREE_CDN}/examples/jsm/libs/draco/gltf/{name}", VENDOR / "addons/libs/draco/gltf" / name,
     DRACO_CREDIT)
    for name in ("draco_decoder.js", "draco_decoder.wasm", "draco_wasm_wrapper.js")
] + [
    ("https://raw.githubusercontent.com/nasa/NASA-3D-Resources/master/3D%20Models/"
     "Mars%202020%20Perseverance%20Rover/Mars%202020%20Perseverance%20Rover.glb",
     SIM_DIR / "rover" / "model" / "perseverance.glb", ROVER_CREDIT),
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError(f"empty response from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    os.replace(part, dest)


def fetch(force: bool = False) -> int:
    manifest_path = SIM_DIR / "assets_sources.json"
    try:
        old = {e["path"]: e for e in json.loads(manifest_path.read_text())["assets"]}
    except (FileNotFoundError, KeyError, ValueError):
        old = {}

    entries, failures = [], 0
    for url, dest, credit in ASSETS:
        rel = dest.relative_to(SIM_DIR).as_posix()
        fetched = False
        if force or not dest.exists() or dest.stat().st_size == 0:
            try:
                _download(url, dest)
                fetched = True
                print(f"fetched  {rel}")
            except Exception as exc:  # keep going; report at the end
                failures += 1
                print(f"FAILED   {rel}: {exc}", file=sys.stderr)
                continue
        else:
            print(f"present  {rel}")
        sha = _sha256(dest)
        prev = old.get(rel)
        when = (prev["time"] if prev and prev.get("sha256") == sha and not fetched
                else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        entries.append({"path": rel, "url": url, "bytes": dest.stat().st_size, "sha256": sha,
                        **credit, "time": when})

    manifest_path.write_text(json.dumps({"three_version": THREE_VERSION, "assets": entries},
                                        indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path.relative_to(SIM_DIR.parent).as_posix()} "
          f"({len(entries)} assets, {failures} failures)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="re-download files that already exist")
    sys.exit(fetch(ap.parse_args(argv).force))


if __name__ == "__main__":
    main()
