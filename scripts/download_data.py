"""Download the v1.0 sources: the HiRISE Jezero DTM, its label, and the MMGIS rover JSON snapshots.

Why a snapshot: MMGIS (mars.nasa.gov/mmgis-maps) sends no CORS headers, so the web app cannot read
it live; M1 freezes it into static files. The DTM download is idempotent (size check against the
remote Content-Length) because it is ~1.8 GB. Provenance (url, size, sha256, retrieval time,
citation) goes to data/metadata/sources.json, which is tracked in git.

Usage: python scripts/download_data.py [--skip-dtm]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _http import download  # noqa: E402
from config import DTM_DIR, DTM_FILENAME, METADATA, PERSEVERANCE_DIR, ROOT, info, read_json, warn, write_json  # noqa: E402

_S3 = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/"
_MMGIS = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/"

SOURCES: dict[str, dict[str, object]] = {
    "hirise_dtm": {
        "url": _S3 + DTM_FILENAME,
        "dest": DTM_DIR / DTM_FILENAME,
        "required": True,
        "dtm": True,
        "citation": (
            "USGS Astrogeology / Mars 2020 TRN team, HiRISE DTM mosaic of Jezero crater, 1 m/post, "
            "MOLA-controlled, heights above the MOLA areoid (Fergason et al. 2020, LPSC 51, #2020)."
        ),
    },
    "hirise_dtm_label": {
        "url": _S3 + DTM_FILENAME.replace(".tif", ".lbl"),
        "dest": DTM_DIR / "JEZ_DTM_1m.lbl",
        "required": False,
        "dtm": True,
        "citation": "ISIS / PDS label of the Jezero HiRISE DTM mosaic (USGS Astrogeology).",
    },
    "m20_waypoints": {
        "url": _MMGIS + "M20_waypoints.json",
        "dest": PERSEVERANCE_DIR / "M20_waypoints.json",
        "required": True,
        "dtm": False,
        "citation": "NASA/JPL-Caltech, Mars 2020 MMGIS rover waypoints (localized positions per sol).",
    },
    "m20_traverse": {
        "url": _MMGIS + "M20_traverse.json",
        "dest": PERSEVERANCE_DIR / "M20_traverse.json",
        "required": True,
        "dtm": False,
        "citation": "NASA/JPL-Caltech, Mars 2020 MMGIS rover traverse (per-sol drive polylines).",
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-dtm", action="store_true", help="do not download the 1.8 GB DTM or its label")
    args = ap.parse_args(argv)

    sources_path = METADATA / "sources.json"
    previous = read_json(sources_path) if sources_path.exists() else {}
    out: dict[str, dict[str, object]] = {}
    failed = False
    for sid, src in SOURCES.items():
        dest = Path(src["dest"])  # type: ignore[arg-type]
        rel = dest.relative_to(ROOT).as_posix()
        if args.skip_dtm and src["dtm"]:
            info(f"  - {sid}: skipped (--skip-dtm)")
            if sid in previous:
                out[sid] = previous[sid]
            continue
        try:
            rec = download(str(src["url"]), dest)
        except (requests.RequestException, OSError) as exc:
            if src["required"] and not dest.exists():
                warn(f"{sid}: download failed: {exc}")
                failed = True
            else:
                warn(f"{sid}: download failed, keeping existing state: {exc}")
            if sid in previous:
                out[sid] = previous[sid]
            continue
        retrieved = rec["retrieved_utc"] or previous.get(sid, {}).get("retrieved_utc")
        if retrieved is None:
            from _http import utc_now

            retrieved = utc_now()
        out[sid] = {
            "url": rec["url"],
            "path": rel,
            "size": rec["size"],
            "sha256": rec["sha256"],
            "retrieved_utc": retrieved,
            "citation": src["citation"],
        }
    write_json(sources_path, out)
    info(f"wrote {sources_path.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
