"""[v1.1] Download the environment & geology sources listed in scripts/config/env_sources.yaml.

Why it never fails on a missing source: every v1.1 dataset is optional context for the v1.0 planner.
A source that is unreachable, needs a human request form (`manual: true`, e.g. the full Mars Climate
Database) or is too large for a quick start (`--skip-large`) is reported and skipped, and the exit
code stays 0 so `pixi run data-env` and the demo launcher keep going. Exit code 1 is reserved for a
corrupt file (e.g. a zip that does not open).

The THEMIS thermal-inertia tile is 2.5 GB; only the AOI window (+ margin) is read through GDAL's
/vsicurl/ driver, which fetches just the 128 x 128 px blocks it needs. MEDA is published per sol;
only the configured subset of sols is fetched.

Usage: python scripts/download_env_data.py [--only id,...] [--skip-large]
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _http import download, sha256_file, utc_now  # noqa: E402
from config import (  # noqa: E402
    AOI,
    METADATA,
    ROOT,
    WEB,
    info,
    load_yaml,
    read_json,
    warn,
    write_json,
)


def _record(src: dict[str, Any], path: Path, url: str, retrieved: str | None, prev: dict[str, Any] | None,
            **extra: Any) -> dict[str, Any]:
    return {
        "url": url,
        "path": path.relative_to(ROOT).as_posix(),
        "size": path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file()),
        "sha256": sha256_file(path) if path.is_file() else None,
        "retrieved_utc": retrieved or (prev or {}).get("retrieved_utc") or utc_now(),
        "citation": src.get("citation"),
        "licence": src.get("licence"),
        "landing_page": src.get("landing_page"),
        **extra,
    }


def fetch_file(sid: str, src: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    dest = ROOT / src["dest"]
    rec = download(src["url"], dest)
    if src.get("unzip"):
        try:
            with zipfile.ZipFile(dest) as zf:
                pats = src.get("extract")
                members = [n for n in zf.namelist() if not pats or any(fnmatch.fnmatch(n, p) for p in pats)]
                todo = [n for n in members if not (dest.parent / n).exists()
                        or (dest.parent / n).stat().st_size != zf.getinfo(n).file_size]
                zf.extractall(dest.parent, members=todo)
        except zipfile.BadZipFile as exc:
            raise CorruptFile(f"{dest.relative_to(ROOT)}: {exc}") from exc
    return _record(src, dest, src["url"], rec["retrieved_utc"], prev)


def fetch_remote_window(sid: str, src: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    """Read the AOI (+ margin) of a big remote raster via /vsicurl/ into a small local GeoTIFF."""
    import rasterio
    from rasterio.windows import from_bounds

    dest = ROOT / src["dest"]
    if dest.exists() and prev and prev.get("url") == src["url"]:
        info(f"  = {dest.name}: up to date (AOI window)")
        return _record(src, dest, src["url"], None, prev, method="vsicurl AOI window")
    from config import MARS_RADIUS_M

    m = float(src.get("margin_deg", 0.05))
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".cub,.tif,.lbl"):
        with rasterio.open("/vsicurl/" + src["url"]) as s:
            info(f"  > {sid}: reading AOI window of {Path(src['url']).name} ({s.width} x {s.height} px remote)")
            k = MARS_RADIUS_M * np.pi / 180.0
            bounds = ((AOI["lon_w"] - m) * k, (AOI["lat_s"] - m) * k, (AOI["lon_e"] + m) * k, (AOI["lat_n"] + m) * k)
            win = from_bounds(*bounds, transform=s.transform).round_offsets().round_lengths()
            arr = s.read(1, window=win)
            profile = {"driver": "GTiff", "width": arr.shape[1], "height": arr.shape[0], "count": 1,
                       "dtype": arr.dtype, "crs": s.crs, "transform": s.window_transform(win),
                       "nodata": s.nodata, "compress": "deflate"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dest, "w", **profile) as d:
        d.write(arr, 1)
    info(f"  + {dest.name}: {arr.shape[1]} x {arr.shape[0]} px")
    return _record(src, dest, src["url"], utc_now(), prev, method="vsicurl AOI window")


def _meda_sols(cfg: dict[str, Any]) -> list[int]:
    sols_cfg = cfg["sols"]
    if isinstance(sols_cfg, list):
        sols = sorted({int(s) for s in sols_cfg})
    else:
        wp = WEB / "waypoints.geojson"
        if not wp.exists():
            raise FileNotFoundError("waypoints.geojson missing (run pixi run data first)")
        sols = sorted({int(f["properties"]["sol"]) for f in read_json(wp)["features"]})
    n = int(cfg.get("max_sols") or len(sols))
    if len(sols) > n:
        idx = np.unique(np.linspace(0, len(sols) - 1, n).round().astype(int))
        sols = [sols[i] for i in idx]
    return sols


def _list_dir(url: str, cache: dict[str, list[str]]) -> list[str]:
    import re

    if url not in cache:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        cache[url] = [h for h in re.findall(r'href="([^"?/][^"]*)"', r.text)]
    return cache[url]


def fetch_meda(sid: str, src: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    """Per-sol MEDA CSVs for the configured products: data/raw/climate/meda/sol_NNNN/*.CSV."""
    cfg = load_yaml("climate.yaml")["meda"]
    base = cfg["base"]
    dest = ROOT / src["dest"]
    sols = _meda_sols(cfg)
    cache: dict[str, list[str]] = {}
    got, missing = 0, 0
    for level in sorted({p["level"] for p in cfg["products"].values()}):
        ranges = [h.rstrip("/") for h in _list_dir(base + level + "/", cache) if h.startswith("sol_") and h.endswith("/")]
        spans = []
        for r in ranges:
            a, b = r.split("_")[1:3]
            spans.append((int(a), int(b), r))
        for sol in sols:
            span = next((s for s in spans if s[0] <= sol <= s[1]), None)
            if span is None:
                missing += 1
                continue
            sol_url = f"{base}{level}/{span[2]}/sol_{sol:04d}/"
            try:
                names = _list_dir(sol_url, cache)
            except requests.RequestException:
                missing += 1
                continue
            for prod in cfg["products"].values():
                if prod["level"] != level:
                    continue
                hits = sorted(n for n in names if n.upper().endswith(".CSV") and f"_{prod['prefix']}_" in n)
                if not hits:
                    continue
                download(sol_url + hits[-1], dest / f"sol_{sol:04d}" / hits[-1], quiet=True)
                got += 1
    info(f"  + meda: {got} files for {len(sols)} sols ({missing} sol/level combinations not published)")
    return _record(src, dest, base, utc_now(), prev, sols=sols, pds_release=src.get("pds_release"))


class CorruptFile(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="comma-separated source ids")
    ap.add_argument("--skip-large", action="store_true", help="skip MEDA bulk, NOAH-H and the THEMIS tile")
    args = ap.parse_args(argv)

    sources: dict[str, dict[str, Any]] = load_yaml("env_sources.yaml")
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    unknown = only - set(sources)
    if unknown:
        warn(f"unknown source id(s): {', '.join(sorted(unknown))}")
    out_path = METADATA / "env_sources.json"
    previous: dict[str, Any] = read_json(out_path) if out_path.exists() else {}
    out = dict(previous)
    skipped: list[str] = []
    corrupt = False

    for sid, src in sources.items():
        if only and sid not in only:
            continue
        kind = src.get("kind", "file")
        if src.get("manual"):
            info(f"  ! {sid}: manual source. Get it from {src['landing_page']} and place it in {src['dest']}")
            if src.get("instructions"):
                info(f"      {src['instructions']}")
            skipped.append(f"{sid} (manual)")
            out.setdefault(sid, {"url": src["url"], "path": src["dest"], "size": None, "sha256": None,
                                 "retrieved_utc": None, "citation": src.get("citation"), "licence": src.get("licence"),
                                 "landing_page": src.get("landing_page"), "manual": True})
            continue
        if kind == "api":
            out[sid] = {"url": src["url"], "path": None, "size": None, "sha256": None,
                        "retrieved_utc": previous.get(sid, {}).get("retrieved_utc"), "citation": src.get("citation"),
                        "licence": src.get("licence"), "landing_page": src.get("landing_page"),
                        "note": "queried at build time by its processing script"}
            continue
        if args.skip_large and src.get("large"):
            info(f"  - {sid}: skipped (--skip-large)")
            skipped.append(f"{sid} (large)")
            continue
        try:
            if kind == "remote_window":
                out[sid] = fetch_remote_window(sid, src, previous.get(sid))
            elif kind == "meda":
                out[sid] = fetch_meda(sid, src, previous.get(sid))
            else:
                out[sid] = fetch_file(sid, src, previous.get(sid))
        except CorruptFile as exc:
            warn(f"{sid}: corrupt download: {exc}")
            corrupt = True
        except Exception as exc:  # noqa: BLE001 - network / server errors degrade to a warning
            local = (ROOT / src["dest"]).exists()
            warn(f"{sid}: unavailable ({type(exc).__name__})" + ("; keeping the local copy" if local else ""))
            skipped.append(f"{sid} ({'offline, cached' if local else 'unavailable'})")

    write_json(out_path, out)
    info(f"wrote {out_path.relative_to(ROOT)}")
    if skipped:
        warn("skipped sources: " + ", ".join(skipped))
    return 1 if corrupt else 0


if __name__ == "__main__":
    sys.exit(main())
