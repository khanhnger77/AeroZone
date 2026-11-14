"""[v1.1] Space-weather static files (contract C10, `space_weather/`).

Why this framing: Mars has no global magnetosphere, so an "electromagnetic storm" at Mars is solar
energetic particles (SEP) and CME shocks hitting the atmosphere, plus auroras over the crustal-field
anomalies. Two products follow:
  * DONKI events. A CME is kept only when a WSA-Enlil run lists Mars in its `impactList` (a modelled
    arrival). SEP and flare events in the window are kept too, but labelled "heliospheric, not
    Mars-confirmed": DONKI detects them near Earth / at STEREO, not at Mars.
  * The crustal magnetic field of Langlais et al. 2019, evaluated from its spherical-harmonic
    coefficients (degree 134) with a NumPy Schmidt semi-normalised Legendre recursion. No extra
    dependency, and the AOI value is honest: Jezero sits on a weakly magnetised crust.

The DONKI API rejects windows longer than 60 days, so the window is fetched in chunks. Any network
failure is a warning; existing files are left untouched.
"""
from __future__ import annotations

import gzip
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _http import get_json, utc_now  # noqa: E402
from config import AOI, MARS_RADIUS_M, ROOT, WEB, info, load_yaml, warn, write_json  # noqa: E402

SW_WEB = WEB / "space_weather"
FLARE_ORDER = {"A": 0, "B": 1, "C": 2, "M": 3, "X": 4}
NOT_CONFIRMED = "heliospheric, not Mars-confirmed"


# ---------------------------------------------------------------- DONKI
def _chunks(start: date, end: date, days: int) -> list[tuple[date, date]]:
    out, s = [], start
    while s <= end:
        e = min(s + timedelta(days=days - 1), end)
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


def _fetch_type(base: str, typ: str, chunks: list[tuple[date, date]], timeout: float,
                extra: dict[str, str] | None = None, workers: int = 6) -> tuple[list[dict[str, Any]], int]:
    """All chunks of one event type, fetched concurrently (the serial run takes minutes)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(chunk: tuple[date, date]) -> list[dict[str, Any]] | None:
        s, e = chunk
        params = {"startDate": s.isoformat(), "endDate": e.isoformat(), **(extra or {})}
        try:
            data = get_json(base + typ, params=params, timeout=timeout)
        except Exception:  # noqa: BLE001
            return None
        return data if isinstance(data, list) else []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, chunks))
    items = [it for r in results if r for it in r]
    return items, sum(r is None for r in results)


def _flare_rank(cls: str | None) -> float:
    if not cls:
        return -1.0
    try:
        return FLARE_ORDER.get(cls[0].upper(), -1) + float(cls[1:] or 0) / 10.0
    except ValueError:
        return float(FLARE_ORDER.get(cls[0].upper(), -1))


def build_donki(cfg: dict[str, Any], src: dict[str, Any]) -> None:
    end = date.today()
    start = end - timedelta(days=round(365.25 * float(cfg["window_years"])))
    chunks = _chunks(start, end, int(cfg["chunk_days"]))
    base, timeout = cfg["base"], float(cfg["timeout_s"])
    info(f"DONKI: {start} .. {end} in {len(chunks)} chunks per type")

    fetched: dict[str, list[dict[str, Any]]] = {}
    total_fail = 0
    for typ in cfg["types"]:
        extra = {"location": "Mars"} if typ == "IPS" else None
        items, fails = _fetch_type(base, typ, chunks, timeout, extra)
        fetched[typ] = items
        total_fail += fails
        if fails:
            warn(f"DONKI {typ}: {fails}/{len(chunks)} chunks failed")
    if total_fail == len(chunks) * len(cfg["types"]):
        warn("DONKI unreachable; donki_mars.json not refreshed")
        return

    cmes = {c.get("activityID"): c for c in fetched.get("CME", [])}
    events: dict[str, dict[str, Any]] = {}
    for sim in fetched.get("WSAEnlilSimulations", []):
        mars = [i for i in (sim.get("impactList") or []) if str(i.get("location", "")).strip() == "Mars"]
        if not mars:
            continue
        for cin in sim.get("cmeInputs") or [{}]:
            cid = cin.get("CMEID") or sim.get("simulationID")
            cme = cmes.get(cid, {})
            glancing = any(i.get("isGlancingBlow") for i in mars)
            note = "WSA-Enlil modelled arrival at Mars" + (" (glancing blow)" if glancing else "")
            if cme.get("note"):
                note += ". " + cme["note"].strip().split("\n")[0][:240]
            if not cin.get("cmeStartTime") and not cme:
                note += " (no CME input listed in the model run; start = model completion time)"
            start_utc = cin.get("cmeStartTime") or cme.get("startTime") or sim.get("modelCompletionTime")
            events[cid] = {"id": cid, "type": "CME", "start_utc": start_utc,
                           "mars_arrival_utc": mars[0].get("arrivalTime"), "speed_km_s": cin.get("speed"),
                           "note": note, "link": cme.get("link") or sim.get("link")}
    for sep in fetched.get("SEP", []):
        inst = ", ".join(i.get("displayName", "") for i in sep.get("instruments") or [])
        events[sep["sepID"]] = {"id": sep["sepID"], "type": "SEP", "start_utc": sep.get("eventTime"),
                                "note": f"{NOT_CONFIRMED}; detected by {inst}" if inst else NOT_CONFIRMED,
                                "link": sep.get("link")}
    min_rank = FLARE_ORDER[str(cfg["flr_min_class"]).upper()[0]]
    for flr in fetched.get("FLR", []):
        if _flare_rank(flr.get("classType")) < min_rank:
            continue
        loc = flr.get("sourceLocation") or "?"
        events[flr["flrID"]] = {"id": flr["flrID"], "type": "FLR", "start_utc": flr.get("beginTime"),
                                "note": f"{flr.get('classType')} flare at {loc}; {NOT_CONFIRMED}", "link": flr.get("link")}
    for ips in fetched.get("IPS", []):
        if str(ips.get("location", "")).strip() != "Mars":
            continue
        iid = ips.get("activityID") or ips.get("eventTime")
        events[iid] = {"id": iid, "type": "IPS", "start_utc": ips.get("eventTime"),
                       "mars_arrival_utc": ips.get("eventTime"), "note": "interplanetary shock observed at Mars",
                       "link": ips.get("link")}

    clean = []
    for ev in sorted(events.values(), key=lambda e: e.get("start_utc") or ""):
        clean.append({k: v for k, v in ev.items() if v is not None and v != ""} | {"note": ev.get("note", ""),
                                                                                   "link": ev.get("link") or src["landing_page"]})
    out = {"category": "observation", "source": base, "retrieved_utc": utc_now(),
           "window": {"start": start.isoformat(), "end": end.isoformat()}, "events": clean,
           "disclaimer": cfg["disclaimer"]}
    write_json(SW_WEB / "donki_mars.json", out)
    by = {t: sum(1 for e in clean if e["type"] == t) for t in ("CME", "SEP", "FLR", "IPS")}
    info(f"wrote space_weather/donki_mars.json ({len(clean)} events: {by})")


# ---------------------------------------------------------------- crustal field
def read_sh(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Parse the Langlais2019.sh(.gz) coefficient table into dense g[n, m], h[n, m] (nT)."""
    opener = gzip.open if path.suffix == ".gz" else open
    rows = []
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) not in (3, 4):
                continue
            try:
                n, m = int(parts[0]), int(parts[1])
                g = float(parts[2])
                h = float(parts[3]) if len(parts) == 4 else 0.0
            except ValueError:
                continue
            if n >= 1 and 0 <= m <= n:
                rows.append((n, m, g, h))
    if not rows:
        raise ValueError("no coefficients found")
    nmax = max(r[0] for r in rows)
    G = np.zeros((nmax + 1, nmax + 1))
    H = np.zeros((nmax + 1, nmax + 1))
    for n, m, g, h in rows:
        G[n, m], H[n, m] = g, h
    return G, H, nmax


def schmidt_legendre(nmax: int, theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Schmidt semi-normalised P[n, m, k] and dP/dtheta for colatitudes theta[k] (radians, off-pole)."""
    c, s = np.cos(theta), np.sin(theta)
    P = np.zeros((nmax + 1, nmax + 1, theta.size))
    P[0, 0] = 1.0
    if nmax >= 1:
        P[1, 1] = s
    for n in range(2, nmax + 1):
        P[n, n] = np.sqrt((2.0 * n - 1.0) / (2.0 * n)) * s * P[n - 1, n - 1]
    for m in range(0, nmax + 1):
        for n in range(m + 1, nmax + 1):
            a = (2.0 * n - 1.0) * c * P[n - 1, m]
            b = np.sqrt((n - 1.0) ** 2 - m * m) * P[n - 2, m] if n - 2 >= m else 0.0
            P[n, m] = (a - b) / np.sqrt(n * n - m * m)
    dP = np.zeros_like(P)
    for n in range(1, nmax + 1):
        for m in range(0, n + 1):
            prev = np.sqrt(n * n - m * m) * P[n - 1, m] if n - 1 >= m else 0.0
            dP[n, m] = (n * c * P[n, m] - prev) / s
    return P, dP


def field_grid(G: np.ndarray, H: np.ndarray, nmax: int, a_km: float, r_km: float,
               lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """|B| (nT) on the lat x lon grid at radius r_km, from internal SH coefficients at radius a_km."""
    theta = np.radians(90.0 - lat_deg)
    phi = np.radians(lon_deg)
    P, dP = schmidt_legendre(nmax, theta)
    n = np.arange(nmax + 1)[:, None]
    m = np.arange(nmax + 1)[None, :]
    ratio = (a_km / r_km) ** (n + 2)                       # (n, 1)
    cos_mp = np.cos(np.outer(np.arange(nmax + 1), phi))    # (m, lon)
    sin_mp = np.sin(np.outer(np.arange(nmax + 1), phi))
    # A[n, m, lon] terms contracted over n first: sum_n f(n) * coef[n, m] * P[n, m, lat]
    Gr, Hr = G * ratio, H * ratio
    br_g = np.einsum("nm,nmk->mk", Gr * (n + 1), P)
    br_h = np.einsum("nm,nmk->mk", Hr * (n + 1), P)
    bt_g = np.einsum("nm,nmk->mk", Gr, dP)
    bt_h = np.einsum("nm,nmk->mk", Hr, dP)
    bp_g = np.einsum("nm,nmk->mk", Gr * m, P)
    bp_h = np.einsum("nm,nmk->mk", Hr * m, P)
    Br = br_g.T @ cos_mp + br_h.T @ sin_mp                  # (lat, lon)
    Bt = -(bt_g.T @ cos_mp + bt_h.T @ sin_mp)
    Bp = (bp_g.T @ sin_mp - bp_h.T @ cos_mp) / np.sin(theta)[:, None]
    return np.sqrt(Br * Br + Bt * Bt + Bp * Bp)


def build_crustal(cfg: dict[str, Any], src: dict[str, Any]) -> None:
    from PIL import Image

    path = ROOT / src["dest"]
    if not path.exists():
        warn("Langlais 2019 coefficients missing (download_env_data.py --only langlais2019); crustal_field.json skipped")
        return
    G, H, nmax = read_sh(path)
    a = float(cfg["reference_radius_km"])
    R = MARS_RADIUS_M / 1000.0
    aoi_lat = np.linspace(AOI["lat_s"], AOI["lat_n"], 9)
    aoi_lon = np.linspace(AOI["lon_w"], AOI["lon_e"], 17)
    by_alt = []
    for alt in cfg["altitudes_km"]:
        b = field_grid(G, H, nmax, a, R + float(alt), aoi_lat, aoi_lon)
        by_alt.append({"altitude_km": float(alt), "aoi_b_nT": {"min": round(float(b.min()), 2),
                                                               "mean": round(float(b.mean()), 2),
                                                               "max": round(float(b.max()), 2)}})
    galt = float(cfg["global_altitude_km"])
    step = float(cfg["global_step_deg"])
    lat = np.arange(90 - step / 2, -90, -step)
    lon = np.arange(-180 + step / 2, 180, step)
    info(f"crustal field: degree {nmax}, global {lat.size} x {lon.size} grid at {galt:g} km ...")
    B = field_grid(G, H, nmax, a, R + galt, lat, lon)
    t = np.clip(np.log1p(B) / np.log1p(float(cfg["png_max_nT"])), 0, 1)
    cols = np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in cfg["colors"]], dtype=float)
    x = t * (len(cols) - 1)
    rgba = np.zeros(B.shape + (4,), dtype=np.uint8)
    for ch in range(3):
        rgba[..., ch] = np.interp(x, np.arange(len(cols)), cols[:, ch]).astype(np.uint8)
    rgba[..., 3] = 210
    png = WEB / "layers" / "crustal_field_global.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(png, optimize=True)

    main_alt = next((x for x in by_alt if x["altitude_km"] == galt), by_alt[0])
    out = {"category": "model", "source": src["landing_page"], "model": cfg["model"],
           "altitude_km": main_alt["altitude_km"], "aoi_b_nT": main_alt["aoi_b_nT"],
           "by_altitude": by_alt, "global_png": "data/layers/crustal_field_global.png",
           "global_bounds": [[-90, -180], [90, 180]], "global_max_nT": round(float(B.max()), 1)}
    write_json(SW_WEB / "crustal_field.json", out)
    info(f"wrote space_weather/crustal_field.json (AOI |B| at {galt:g} km: {main_alt['aoi_b_nT']}) and "
         f"layers/crustal_field_global.png")


def main() -> int:
    cfg = load_yaml("space_weather.yaml")
    env = load_yaml("env_sources.yaml")
    SW_WEB.mkdir(parents=True, exist_ok=True)
    try:
        build_donki(cfg["donki"], env["donki"])
    except Exception as exc:  # noqa: BLE001 - optional network source
        warn(f"DONKI step failed ({type(exc).__name__}: {exc})")
    build_crustal(cfg["crustal_field"], env["langlais2019"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
