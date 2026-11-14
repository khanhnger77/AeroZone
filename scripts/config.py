"""Shared configuration for the MARS ODYSSEY data pipeline.

All paths, the study-area (AOI) definition and the Mars reference body live here so every
script uses the same values.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
ANALYSIS = DATA / "analysis"
METADATA = DATA / "metadata"
WEB = ROOT / "frontend" / "public" / "data"  # static files served to the browser

# Make GDAL/PROJ work without `conda activate`.
_env_share = Path(os.__file__).resolve().parents[2] / "share"
os.environ.setdefault("PROJ_DATA", str(_env_share / "proj"))
os.environ.setdefault("GDAL_DATA", str(_env_share / "gdal"))

# --- Mars reference body -----------------------------------------------------------------
# IAU Mars 2000 sphere, as used by the USGS Mars 2020 TRN products (see the .lbl label).
MARS_RADIUS_M = 3_396_190.0
# Coordinates: planetocentric latitude, east-positive longitude, -180..180 domain.

# --- Study area (AOI) ----------------------------------------------------------------------
# Jezero delta front + Perseverance traverse sols ~0-1215 (landing site, Seitah,
# delta front, upper fan, Cheyava Falls). Fully inside the HiRISE TRN DTM extent
# (77.2229-77.5840 E, 18.3068-18.6693 N). Verified in validate_data.py.
AOI = {"lon_min": 77.29, "lon_max": 77.47, "lat_min": 18.42, "lat_max": 18.505}

# Analysis grid resolution (metres in the source projection). The DTM is 1 m/post; we
# aggregate to 5 m for slope (10 m baseline) and routing so the grid stays ~2 M cells.
ANALYSIS_RES_M = 5.0


def deg_to_proj(lon: float, lat: float) -> tuple[float, float]:
    """lon/lat (deg) -> Equirectangular (clat=0, clon=0) metres on the Mars sphere."""
    return MARS_RADIUS_M * math.radians(lon), MARS_RADIUS_M * math.radians(lat)


def proj_to_deg(x: float, y: float) -> tuple[float, float]:
    return math.degrees(x / MARS_RADIUS_M), math.degrees(y / MARS_RADIUS_M)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance on the Mars sphere (metres)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * MARS_RADIUS_M * math.asin(math.sqrt(a))
