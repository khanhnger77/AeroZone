"""Seed campaign: drive a whole route with random boulders and craters for many seeds.

usage: python -m sim.obstacle_avoid.tests.campaign --seeds 1-48 --model wheeled --speed 2 --warp 5 [--jobs 8]

Route and terrain: the **real** demo mission when M4 (``sim.server.terrain``), M1's
``data/processed/elevation_5m.tif`` and M2's planner (``/api/route`` on :8000) are all available;
otherwise a synthetic stand-in (rolling terrain, 1.5 km curved route), clearly labelled in the output.
A seed PASSES when the rover arrives, never touches a boulder, never puts its footprint inside a crater
rim, never ends ``blocked`` and finishes in ``track`` mode.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
LANDING = (77.45088572, 18.44462715)
CHEYAVA = (77.3052, 18.4975)


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def real_mission():
    """(grid, path_xy, label) from M1/M2/M4, or None when any piece is missing."""
    try:
        import httpx
        import yaml
        from sim.server.terrain import TerrainSource, build_mission_terrain  # M4 (C8)
        tif = ROOT / "data" / "processed" / "elevation_5m.tif"
        if not tif.exists():
            return None
        r = httpx.post("http://127.0.0.1:8000/api/route", timeout=60.0,
                       json={"start": {"lon": LANDING[0], "lat": LANDING[1]},
                             "end": {"lon": CHEYAVA[0], "lat": CHEYAVA[1]}})
        r.raise_for_status()
        rr = r.json()
        route = next(x for x in rr["routes"] if x["id"] == rr["recommended"])
        cfg = yaml.safe_load((ROOT / "sim" / "configs" / "physics.yaml").read_text())
        frame, corridor, _ = build_mission_terrain(TerrainSource(tif), route["coordinates"], cfg)
        xy = np.array([frame.to_local(lon, lat) for lon, lat in route["coordinates"]])
        return corridor, xy, f"real: Jezero demo route ({route['id']}, {route['length_m']:.0f} m)"
    except Exception:
        return None


def synthetic_mission():
    from sim.obstacle_avoid.tests.stubs import make_grid
    g = make_grid(lambda X, Y: 6.0 * np.sin(X / 140.0) + 4.0 * np.cos(Y / 95.0) + 0.004 * X,
                  -300, 1700, -700, 700, d=5.0)
    s = np.arange(0.0, 1500.0, 2.0)
    xy = np.stack([s, 180.0 * np.sin(s / 260.0)], axis=1)
    return g, xy, "synthetic stand-in (rolling terrain, 1.5 km curved route)"


def one(args) -> dict:
    seed, model, speed, warp, use_real = args
    import dataclasses
    from sim.obstacle_avoid.tests.stubs import run_closed_loop
    m = (real_mission() if use_real else None) or synthetic_mission()
    grid, xy, label = m
    grid = dataclasses.replace(grid, h=grid.h.copy())
    t0 = time.time()
    res, av = run_closed_loop(grid, xy, model=model, speed=speed, warp=warp, seed=seed)
    return {"seed": seed, "pass": res.ok, "label": label, "rocks": len(av.rocks), "craters": len(av.craters),
            "contacts": res.contacts, "blocked": res.blocked, "arrived": res.arrived, "final_mode": res.final_mode,
            "min_gap_m": round(res.min_gap_m, 3) if np.isfinite(res.min_gap_m) else None,
            "min_rim_margin_m": round(res.min_rim_margin_m, 3) if np.isfinite(res.min_rim_margin_m) else None,
            "detours": res.detours, "recoveries": res.recoveries, "sim_s": round(res.sim_time_s, 1),
            "wall_s": round(time.time() - t0, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-48")
    ap.add_argument("--model", default="wheeled", choices=["wheeled", "kinematic"])
    ap.add_argument("--speed", type=float, default=2.0)
    ap.add_argument("--warp", type=float, default=5.0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--synthetic", action="store_true", help="skip the real route even if available")
    ap.add_argument("--out", default=None, help="write the per-seed results as JSON")
    a = ap.parse_args()
    use_real = not a.synthetic and real_mission() is not None
    seeds = parse_seeds(a.seeds)
    jobs = [(s, a.model, a.speed, a.warp, use_real) for s in seeds]
    rows = []
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for r in ex.map(one, jobs):
            rows.append(r)
            print(f"seed {r['seed']:3d}  {'PASS' if r['pass'] else 'FAIL'}  rocks {r['rocks']:2d}  craters {r['craters']}"
                  f"  gap {r['min_gap_m']}  rim {r['min_rim_margin_m']}  detours {r['detours']}"
                  f"  rec {r['recoveries']}  {r['final_mode']}  sim {r['sim_s']}s  wall {r['wall_s']}s", flush=True)
    gaps = [r["min_gap_m"] for r in rows if r["min_gap_m"] is not None]
    rims = [r["min_rim_margin_m"] for r in rows if r["min_rim_margin_m"] is not None]
    summary = {"route": rows[0]["label"] if rows else None, "model": a.model, "speed_m_s": a.speed, "warp": a.warp,
               "passed": sum(r["pass"] for r in rows), "total": len(rows),
               "boulders": sum(r["rocks"] for r in rows), "craters": sum(r["craters"] for r in rows),
               "contacts": sum(r["contacts"] for r in rows), "blocked": sum(r["blocked"] for r in rows),
               "closest_boulder_gap_m": min(gaps) if gaps else None,
               "closest_rim_margin_m": min(rims) if rims else None}
    print(json.dumps(summary, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "seeds": rows}, indent=1))


if __name__ == "__main__":
    main()
