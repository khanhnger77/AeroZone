"""Download the raw NASA/USGS source datasets and record provenance.

Idempotent: files already present with the expected size are skipped.
Writes data/metadata/sources.json (URL, size, sha256, retrieval time, citation).

Run:  python scripts/download_data.py          (all)
      python scripts/download_data.py --skip-dtm
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys

import requests

from config import METADATA, RAW

S3 = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE"
MMGIS = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json"

SOURCES = [
    {
        "id": "hirise_dtm",
        "file": RAW / "hirise_dtm" / "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif",
        "url": f"{S3}/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif",
        "kind": "observation-derived (stereo photogrammetry DTM)",
        "citation": "Fergason, R.L., Galuszka, D.M., Hare, T.M., Mayer, D.P., Redding, B.L. (2020). "
                    "Mars 2020 Terrain Relative Navigation HiRISE DTM Mosaic. USGS Astrogeology. "
                    "doi:10.5066/P9REJ9JN (CC0)",
        "big": True,
    },
    {
        "id": "hirise_dtm_label",
        "file": RAW / "hirise_dtm" / "JEZ_DTM_1m.lbl",
        "url": "https://astrogeology.usgs.gov/ckan/dataset/381ba037-d8a9-4cea-86be-374e4951afe3/resource/"
               "bd39aeaf-231d-4732-b333-7636bce607ff/download/"
               "jez_hirise_soc_006_dtm_molatopography_deltageoid_1m_eqc_latts0_lon0_blend40.lbl",
        "kind": "metadata (ISIS label)",
        "citation": "USGS Astrogeology, doi:10.5066/P9REJ9JN",
    },
    {
        "id": "m20_waypoints",
        "file": RAW / "perseverance" / "M20_waypoints.json",
        "url": f"{MMGIS}/M20_waypoints.json",
        "kind": "observation (rover localization)",
        "citation": "NASA/JPL-Caltech Mars 2020 MMGIS, Perseverance waypoints",
    },
    {
        "id": "m20_traverse",
        "file": RAW / "perseverance" / "M20_traverse.json",
        "url": f"{MMGIS}/M20_traverse.json",
        "kind": "observation (rover traverse)",
        "citation": "NASA/JPL-Caltech Mars 2020 MMGIS, Perseverance traverse path",
    },
]

HEADERS = {"User-Agent": "mars-odyssey-hackathon/0.1"}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def download(src: dict) -> dict:
    path = src["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    head = requests.head(src["url"], headers=HEADERS, allow_redirects=True, timeout=60)
    remote_size = int(head.headers.get("Content-Length", 0)) or None
    if path.exists() and (remote_size is None or path.stat().st_size == remote_size):
        print(f"[skip] {path.name} already present")
    else:
        print(f"[get ] {src['url']}")
        with requests.get(src["url"], headers=HEADERS, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(path)
    return {
        "id": src["id"],
        "url": src["url"],
        "local_path": str(path.relative_to(RAW.parent.parent)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "kind": src["kind"],
        "citation": src["citation"],
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-dtm", action="store_true", help="skip the 1.8 GB DTM")
    args = ap.parse_args()
    METADATA.mkdir(parents=True, exist_ok=True)
    out = METADATA / "sources.json"
    records = json.loads(out.read_text()) if out.exists() else {}
    for src in SOURCES:
        if args.skip_dtm and src.get("big"):
            continue
        records[src["id"]] = download(src)
    out.write_text(json.dumps(records, indent=2))
    print(f"provenance -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
