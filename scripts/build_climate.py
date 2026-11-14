"""[v1.1] Climate and weather static files for the web app (contract C10, `climate/`).

Why charts, not rasters: weather at Jezero is either a model value for one climate-model cell (MCD,
a GCM cell is far larger than the AOI) or a point measurement at the rover (MEDA), and dust storms
are a regional catalogue (MDAD). None of them has 5 m spatial structure, so they are written as
series and summaries the UI shows as charts and popups, each tagged with its `category`.

Steps (each optional, a missing input is a warning):
  1. MCD export (placed by hand in data/raw/climate/mcd/) -> climate/mcd_jezero.json
  2. MEDA per-sol CSVs -> climate/meda_sols.json, then the `meda` property on waypoints.geojson
  3. MDAD catalogue -> climate/dust_storms.geojson, dust_storm_stats.json,
     layers/dust_storm_frequency_global.png
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _http import utc_now  # noqa: E402
from config import (  # noqa: E402
    AOI_CENTRE,
    METADATA,
    RAW,
    ROOT,
    WEB,
    haversine_m,
    info,
    load_yaml,
    normalise_lon,
    read_json,
    warn,
    write_json,
)

CLIMATE_WEB = WEB / "climate"


def _env_meta(sid: str) -> dict[str, Any]:
    p = METADATA / "env_sources.json"
    return (read_json(p).get(sid) or {}) if p.exists() else {}


def _clean(v: float, nd: int = 3) -> float | None:
    return round(float(v), nd) if v is not None and np.isfinite(v) else None


# ---------------------------------------------------------------- (1) MCD
def _read_mcd_ascii(path: Path) -> dict[str, np.ndarray]:
    """Parse an MCD web-interface / generic ASCII export: a header row of names, then numbers."""
    header: list[str] | None = None
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            cand = re.split(r"[\s,;|]+", s.lstrip("#").strip())
            if any(c.lower() in ("ls", "ls_deg", "solar_longitude") for c in cand):
                header = cand
            continue
        parts = re.split(r"[\s,;]+", s)
        try:
            rows.append([float(x) for x in parts])
        except ValueError:
            header = parts
    if header is None or not rows:
        raise ValueError("no header with an Ls column")
    arr = np.array([r[: len(header)] for r in rows if len(r) >= len(header)], dtype=float)
    return {h.lower(): arr[:, i] for i, h in enumerate(header)}


def _read_mcd_netcdf(path: Path) -> dict[str, np.ndarray]:
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(str(path))
    out: dict[str, np.ndarray] = {}
    subs = ds.GetSubDatasets() or [(str(path), "")]
    for name, _ in subs:
        var = name.split(":")[-1].strip('"')
        try:
            sub = gdal.Open(name)
            out[var.lower()] = sub.ReadAsArray().astype(float).ravel()
        except RuntimeError:
            continue
    md = ds.GetMetadata() or {}
    for k, v in md.items():
        if k.lower().startswith("ls#") or k.lower() in ("ls", "nc_global#ls"):
            try:
                out.setdefault("ls", np.array([float(x) for x in re.findall(r"[-\d.]+", v)]))
            except ValueError:
                pass
    return out


def build_mcd(cfg: dict[str, Any], env: dict[str, Any]) -> None:
    src = env["mcd"]
    d = ROOT / src["dest"]
    ls_grid = np.arange(cfg["ls_deg"]["start"], cfg["ls_deg"]["stop"] + 1, cfg["ls_deg"]["step"], dtype=float)
    series: dict[str, dict[str, list[float | None]]] = {}
    for scen in cfg["scenarios"]:
        files = sorted(d.glob(f"mcd_{scen}.*")) if d.exists() else []
        if not files:
            continue
        f = files[0]
        try:
            cols = _read_mcd_netcdf(f) if f.suffix.lower() in (".nc", ".nc4", ".cdf") else _read_mcd_ascii(f)
        except Exception as exc:  # noqa: BLE001
            warn(f"MCD {f.name}: unreadable ({exc}); scenario {scen} skipped")
            continue
        ls = next((cols[k] for k in ("ls", "ls_deg", "solar_longitude") if k in cols), None)
        entry: dict[str, list[float | None]] = {}
        for key, names in cfg["variables"].items():
            vals = next((cols[n.lower()] for n in names if n.lower() in cols), None)
            if vals is None:
                entry[key] = [None] * len(ls_grid)
                continue
            if ls is not None and len(ls) == len(vals):
                order = np.argsort(ls)
                v = np.interp(ls_grid, ls[order], vals[order], period=360.0)
            elif len(vals) == len(ls_grid):
                v = vals
            else:
                warn(f"MCD {f.name}: {key} has {len(vals)} values and no Ls axis; skipped")
                v = np.full(len(ls_grid), np.nan)
            entry[key] = [_clean(x) for x in v]
        series[scen] = entry
    if not series:
        warn(f"no MCD export in {src['dest']}; mcd_jezero.json skipped. Request access at {src['landing_page']}")
        return
    out = {
        "category": "model", "source": src["url"], "mcd_version": str(cfg["version"]), "retrieved_utc": utc_now(),
        "location": {"lon": round(AOI_CENTRE[0], 4), "lat": round(AOI_CENTRE[1], 4)},
        "local_time_h": float(cfg["local_time_h"]), "ls_deg": [float(x) for x in ls_grid],
        "scenarios": list(series), "series": series, "disclaimer": cfg["disclaimer"],
    }
    write_json(CLIMATE_WEB / "mcd_jezero.json", out)
    info(f"wrote climate/mcd_jezero.json ({', '.join(series)})")


# ---------------------------------------------------------------- (2) MEDA
def _read_cols(path: Path, prod: dict[str, Any]) -> np.ndarray:
    """Numeric columns of one MEDA CSV; values outside `valid_range` (fill values) become NaN."""
    import pandas as pd

    head = pd.read_csv(path, nrows=0).columns
    use = [c for c in prod["columns"] if c in head]
    if not use:
        return np.empty((0, 0))
    df = pd.read_csv(path, usecols=use, low_memory=False)
    a = df.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float, copy=True)
    if prod.get("valid_range"):
        lo, hi = prod["valid_range"]
        a[(a < lo) | (a > hi)] = np.nan
    return a


def build_meda(cfg: dict[str, Any], env: dict[str, Any]) -> dict[int, dict[str, float]]:
    src = env["meda"]
    root = ROOT / src["dest"]
    sol_dirs = sorted(root.glob("sol_*")) if root.exists() else []
    if not sol_dirs:
        warn("no MEDA files (download_env_data.py --only meda); meda_sols.json skipped")
        return {}
    prods = cfg["products"]
    sols = []
    for sd in sol_dirs:
        sol = int(sd.name.split("_")[1])

        def find(prefix: str) -> Path | None:
            hits = sorted(p for p in sd.glob("*.CSV") if f"_{prefix}_" in p.name)
            return hits[-1] if hits else None

        rec: dict[str, Any] = {"sol": sol}
        try:
            p_file, t_file = find(prods["pressure"]["prefix"]), find(prods["air_temp"]["prefix"])
            if p_file is None or t_file is None:
                continue
            p = _read_cols(p_file, prods["pressure"])
            t = _read_cols(t_file, prods["air_temp"])
            pv = p[:, 0][np.isfinite(p[:, 0])] if p.size else np.array([])
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                tv = np.nanmean(t, axis=1) if t.size else np.array([])
            tv = tv[np.isfinite(tv)]
            if not pv.size or not tv.size:
                continue
            rec.update({"t_air_mean_k": _clean(tv.mean(), 2), "t_air_min_k": _clean(tv.min(), 2),
                        "t_air_max_k": _clean(tv.max(), 2), "p_mean_pa": _clean(pv.mean(), 2)})
            for key, prod, nd in (("rh_mean_pct", "humidity", 3), ("wind_mean_m_s", "wind", 2)):
                f = find(prods[prod]["prefix"])
                if f is not None:
                    a = _read_cols(f, prods[prod])
                    v = a[:, 0][np.isfinite(a[:, 0])] if a.size else np.array([])
                    if v.size:
                        rec[key] = _clean(v.mean(), nd)
            rec["n_samples"] = int(pv.size)
        except Exception as exc:  # noqa: BLE001 - one malformed sol must not drop the others
            warn(f"MEDA sol {sol}: {exc}")
            continue
        sols.append(rec)
    meta = _env_meta("meda")
    out = {"category": "observation", "source": src["landing_page"], "pds_release": src.get("pds_release", ""),
           "retrieved_utc": meta.get("retrieved_utc") or utc_now(), "sols": sols}
    write_json(CLIMATE_WEB / "meda_sols.json", out)
    info(f"wrote climate/meda_sols.json ({len(sols)} sols)")
    return {s["sol"]: {"t_air_mean_k": s["t_air_mean_k"], "p_mean_pa": s["p_mean_pa"]} for s in sols}


def attach_meda_to_waypoints(by_sol: dict[int, dict[str, float]]) -> None:
    p = WEB / "waypoints.geojson"
    if not by_sol or not p.exists():
        return
    wp = read_json(p)
    n = 0
    for f in wp["features"]:
        m = by_sol.get(int(f["properties"]["sol"]))
        if m:
            f["properties"]["meda"] = m
            n += 1
    write_json(p, wp, compact=True)
    info(f"added `meda` to {n} waypoints")


# ---------------------------------------------------------------- (3) MDAD
def build_mdad(cfg: dict[str, Any], env: dict[str, Any]) -> None:
    import pandas as pd
    from PIL import Image

    src = env["mdad"]
    path = ROOT / src["dest"]
    if not path.exists():
        warn("MDAD catalogue missing (download_env_data.py --only mdad); dust storm files skipped")
        return
    c = cfg["columns"]
    df = pd.read_csv(path, sep="\t", quotechar='"', skipinitialspace=True)
    df.columns = [str(x).strip() for x in df.columns]
    lon = normalise_lon(pd.to_numeric(df[c["lon"]], errors="coerce").to_numpy())
    lat = pd.to_numeric(df[c["lat"]], errors="coerce").to_numpy(dtype=float)
    ls = pd.to_numeric(df[c["ls"]], errors="coerce").to_numpy(dtype=float)
    my = pd.to_numeric(df[c["mars_year"]], errors="coerce").to_numpy(dtype=float)
    area = pd.to_numeric(df[c["area_km2"]], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(ls) & np.isfinite(my)
    dist_km = haversine_m(AOI_CENTRE[0], AOI_CENTRE[1], lon, lat) / 1000.0
    radius = float(cfg["radius_km"])
    near = ok & (dist_km <= radius)

    def s(v: Any) -> str:
        return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()

    feats = []
    for i in np.nonzero(near)[0]:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon[i]), 3), round(float(lat[i]), 3)]},
                      "properties": {"seq_id": s(df[c["seq_id"]].iloc[i]), "member_id": s(df[c["member_id"]].iloc[i]),
                                     "mars_year": int(my[i]), "ls_deg": round(float(ls[i]), 2),
                                     "area_km2": round(float(area[i]), 1) if np.isfinite(area[i]) else None,
                                     "distance_km": round(float(dist_km[i]), 1), "source": src["landing_page"]}})
    write_json(CLIMATE_WEB / "dust_storms.geojson", {"type": "FeatureCollection", "features": feats}, compact=True)

    step = float(cfg["ls_bin_deg"])
    bins = np.arange(0, 360, step)
    ls_near = ls[near] % 360.0
    by_ls = [{"ls_start_deg": float(b), "count": int(((ls_near >= b) & (ls_near < b + step)).sum())} for b in bins]
    years = sorted({int(y) for y in my[ok]})
    by_year = [{"mars_year": y, "count": int((my[near] == y).sum())} for y in years]

    # global 1-degree member-count grid
    grid = np.zeros((180, 360), dtype=np.int32)
    row = np.clip((90.0 - lat[ok]).astype(int), 0, 179)
    col = np.clip((lon[ok] + 180.0).astype(int), 0, 359)
    np.add.at(grid, (row, col), 1)
    rgba = np.zeros((180, 360, 4), dtype=np.uint8)
    if grid.max() > 0:
        t = np.log1p(grid) / np.log1p(grid.max())
        stops = np.array([[255, 245, 235], [253, 174, 107], [241, 105, 19], [166, 54, 3], [127, 39, 4]], dtype=float)
        x = t * (len(stops) - 1)
        for ch in range(3):
            rgba[..., ch] = np.interp(x, np.arange(len(stops)), stops[:, ch]).astype(np.uint8)
        rgba[..., 3] = np.where(grid > 0, 200, 0)
    png = WEB / "layers" / "dust_storm_frequency_global.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(png, optimize=True)

    stats = {"category": "observation", "source": src["landing_page"], "radius_km": radius,
             "years": [years[0], years[-1]] if years else [24, 32], "by_ls_bin": by_ls, "by_mars_year": by_year,
             "global_frequency_png": "data/layers/dust_storm_frequency_global.png",
             "global_bounds": [[-90, -180], [90, 180]]}
    write_json(CLIMATE_WEB / "dust_storm_stats.json", stats)
    info(f"wrote climate/dust_storms.geojson ({len(feats)} members within {radius:g} km), dust_storm_stats.json, "
         f"layers/dust_storm_frequency_global.png")


def main() -> int:
    cfg = load_yaml("climate.yaml")
    env = load_yaml("env_sources.yaml")
    CLIMATE_WEB.mkdir(parents=True, exist_ok=True)
    (RAW / "climate").mkdir(parents=True, exist_ok=True)
    build_mcd(cfg["mcd"], env)
    attach_meda_to_waypoints(build_meda(cfg["meda"], env))
    build_mdad(cfg["mdad"], env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
