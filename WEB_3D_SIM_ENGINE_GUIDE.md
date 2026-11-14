# Web 3D sim engine guide

How the MARS ODYSSEY simulation engine (module M4, `sim/`) is designed, and why. This is the
reference for the nine design rules the team agreed on. Each rule gives what we do, why we do it,
where it lives in the code, and how we checked it.

For how to run the sim, see [`sim/README.md`](sim/README.md). The interfaces between modules
(C1–C10) are defined in the team contracts; when this guide and a contract disagree, the contract
wins.

## The system in one picture

```
 browser (Three.js view)                         sim server (aiohttp, port 8100, under /sim)
 ┌──────────────────────────┐   GET /sim/config    ┌────────────────────────────────────────────┐
 │ main.js  load → build    │ ───────────────────► │ discovery document                         │
 │ terrain.js meshes        │   GET api/mission    │ mission build (terrain, M5 obstacles, rover)│
 │ rover.js NASA model      │   GET api/terrain/*  │                                            │
 │ net.js   socket + buffer │ ◄─── WS telemetry ── │ 50 Hz loop:                                │
 │                          │   50 Hz, sim_time    │   pose → M5 tick → navigator → M5 command  │
 │ renders the server pose  │ ──── WS commands ──► │   → PyBullet fixed steps → telemetry       │
 └──────────────────────────┘                      └────────────────────────────────────────────┘
```

---

## Rule 1. Physics on the server; the browser is a view

**What.** The server owns physics, the synthetic sensors and the authoritative rover state. The
browser renders the pose the server sends and never integrates motion itself. If telemetry stops for
more than 1.5 s, the page shows "telemetry stale" rather than guessing.

**Why.** Two simulators drift apart. With one authority, every viewer (the standalone page, the
planner's embedded view and M3's lidar screen) shows the same rover at the same place. The
browser's frame rate and the physics rate are also decoupled: a slow laptop renders fewer frames,
but the physics stays correct.

**Where.** `sim/server/physics.py` (state), `sim/server/sim_server.py` (loop and streaming),
`sim/web/js/net.js` (`InterpBuffer`), `sim/web/js/main.js` (render only).

## Rule 2. The simulator only simulates

**What.** Driving decisions live behind a narrow interface: the backend receives the pose and
returns a speed and turn rate `(v, ω)`. Today that backend is the pure-pursuit navigator
(`sim/backend/navigator.py`). Obstacle avoidance (M5) wraps it through the C8 facade:
`avoid.command(nav.command(), pose, nav)` passes the navigator's command through, or overrides it
while avoiding.

**Why.** It keeps autonomy and physics separately testable and replaceable. A real autonomy stack
could replace the navigator without touching physics or rendering, and M5 was built in parallel
against a fixed interface.

**How pure pursuit works here.**
- Progress is the closest point on the route, searched only within the next 60 segments. This keeps
  progress monotone and stops a route that doubles back from making the rover jump ahead.
- The look-ahead is `ld = 4 m + 3 s × |v|`. The turn rate is `ω = v · 2 sin(α) / ld`, clipped to
  0.35 rad/s, where α is the angle from the heading to the look-ahead point.
- If the look-ahead point is more than 60° off the nose, the rover stops and turns in place, which
  its four-corner steering allows.
- Speed is `cruise × clip(remaining / 8 m, 0.15, 1)`, so the rover eases into the goal.

**Evidence.** On a 250 m sinusoidal route over curved terrain (`sim/tests/test_navigator.py`):

| Model | Cruise | Max cross-track | RMS cross-track |
|---|---|---|---|
| wheeled (PyBullet) | 1 m/s | 0.07 m | 0.02 m |
| wheeled (PyBullet) | 2 m/s | 0.29 m | 0.07 m |
| kinematic | 1 m/s | 0.02 m | 0.01 m |
| kinematic | 2 m/s | 0.04 m | 0.03 m |

The test only requires < 2.5 m and arrival.

## Rule 3. One PyBullet client, fixed time step, capped substeps

**What.** Each server process has one PyBullet `DIRECT` client. Physics advances in fixed 5 ms
steps. Each loop tick adds `elapsed × warp` to an accumulator and consumes it in whole steps, at
most `cap = min(8 × max(warp, 1), 400)` per tick. If the cap is reached, the leftover backlog is
**dropped**, not carried forward.

**Why.**
- A fixed step keeps contact dynamics stable and reproducible. A variable step changes the
  physics whenever the machine is busy.
- Without a cap, a stall makes the next tick try to catch up, which takes longer, which makes the
  next stall worse (the "spiral of death"). Dropping the backlog trades a moment of lost sim time for
  a loop that always recovers.
- PyBullet is not thread-safe, so every physics call runs on the event-loop thread. Only
  physics-free work (the GeoTIFF read, texture mosaics) runs in a thread pool.

**Evidence.**
- At 1×, a 20 ms tick takes 4 substeps; at 5× it takes 20 (`test_accumulator_caps_and_drops_backlog`).
- In a real-time loop at 5× warp, the measured effective rate was **4.92×** (acceptance target:
  about 4.9×).

## Rule 4. Configuration, not constants

**What.** Gravity, atmosphere, contact parameters, rover dimensions and limits, and navigator gains
all live in `sim/configs/physics.yaml` and `sim/configs/rover.yaml`. Mars gravity is 3.711 m/s².

**Why.** One wrong literal, such as Earth's 9.81, silently invalidates every result. Keeping values
in YAML makes them visible, reviewable and shared with the browser through `/sim/config`.

**Evidence.** `test_mars_gravity_in_config` and `test_no_earth_gravity_literal` (which fails if
`9.81` appears in `physics.py`). A free-falling sphere reaches `−3.711 × t` within 0.1 %.

## Rule 5. One terrain grid for the renderer and the collider

**What.** The drive corridor (route bounding box + 250 m, native 5 m posts, at most 2200 per side)
is read once into a `Grid`. That exact grid becomes:
- the PyBullet heightfield,
- the bytes served at `/sim/api/terrain/corridor.bin`,
- the browser's detailed mesh,
- the surface M5's lidar ray-casts against.

When obstacles are enabled, M5 carves craters into this grid **before** the collider is built and
before it is served.

**Why.** "What you see is what you hit." If the drawn terrain and the collider differ, the rover
floats above or sinks into the visible ground, and lidar returns contradict the picture.

**Details that matter.**
- Bullet centres a heightfield on its x/y extent *and* on its height range. The body is therefore
  placed at the grid's x/y centre and at `z = (h_min + h_max) / 2`.
- Rows run south → north and columns west → east (C5). East-west spacing is `5 m × cos(lat0)`,
  because an east-west projected metre on the equirectangular raster is `cos(lat)` ground metres
  (0.949 at Jezero).
- The frame origin is the corridor centre; `z0` is the corridor's mean elevation, rounded to 0.1 m.
  Heights stay small, which keeps float32 precision near the rover.
- A coarser context grid (the whole study area at 10 m) is visual only. Its mesh has a hole one
  context cell inside the corridor edge and is lowered 0.3 m, so the detailed corridor always wins
  in the overlap band and there are no cracks.

**Evidence.** On curved terrain, ray casts against the collider match the grid's bilinear heights
within **0.15 m** at 300 random points (`test_collider_matches_render_grid`; the remaining
difference is Bullet's two-triangle split of each cell). The server tests compare the served bytes
exactly with the collider's grid.

## Rule 6. A discovery document; no hard-coded endpoints

**What.** The page reads `/sim/config` first. That document gives the protocol, physics settings,
rover geometry and model URL, the available controls and the current mission. Every URL in the page
is relative (`config`, `api/mission`, `ws`, `vendor/three/...`).

**Why.** The page is served directly at `/sim/` and behind the planner's Vite proxy at `/.sim/`.
Relative URLs make both work with no configuration. The browser also learns, for example, which
physics models exist, instead of assuming.

## Rule 7. Telemetry stamped with sim time, replayed through a buffer

**What.** Each telemetry message carries `sim_time`. The browser plays it back about 220 ms behind
the newest sample, interpolating position linearly and orientation by slerp. The playback clock runs
at the current warp, so changing the warp needs no special handling.

**Why.** WebSocket messages arrive in bursts. Rendering each one as it arrives makes the rover
stutter; a short replay delay smooths it at the cost of a lag of about a fifth of a second.

## Rule 8. Pause when nobody watches; close sockets before cleanup

**What.** The loop does not step when there is no mission or no connected client. On shutdown the
server closes all WebSockets first, then stops the loop and disconnects PyBullet.

**Why.** It avoids burning CPU on an unseen simulation and keeps the rover where the user left it.
Closing sockets first lets clients see a clean close (and reconnect later) instead of a hang.

**Evidence.** `test_no_stepping_without_clients`: sim time stays frozen with no client and advances
once a socket connects.

## Rule 9. Vendor Three.js locally

**What.** three.js r170 and the add-ons used (GLTF and Draco loaders, OrbitControls,
BufferGeometryUtils, the Draco decoder) are committed under `sim/web/vendor/three/` and loaded
through an import map. The NASA Perseverance model is committed at
`sim/rover/model/perseverance.glb`. `python -m sim.tools.fetch_assets` re-creates both and records
provenance in `sim/assets_sources.json`.

**Why.** No CDN at runtime: the demo works offline and on venue networks, and the three.js version
cannot change underneath us. The static handler sets MIME types explicitly (`.js` →
`text/javascript`, `.wasm` → `application/wasm`), because Windows registries often map `.js` to
`text/plain`, which browsers refuse for ES modules.

---

## Vehicle model decisions

The wheeled model is a PyBullet multibody: a box chassis, six wheels, and steering links on the
four corner wheels.

| Decision | Reason |
|---|---|
| **Sphere** wheel collision shapes | Cylinder-vs-heightfield contacts jitter in Bullet; spheres give a clean single contact |
| Steering by **instantaneous centre of rotation** | For a wheel at `(xᵢ, yᵢ)`: `vx = v − ω·yᵢ`, `vy = ω·xᵢ`. Corner wheels point along `atan2(vy, vx)`, flipped by π to stay within ±90° with the spin reversed; middle wheels spin at `vx / r` |
| Wheel spin × `cos(steer error)` | A wheel is not driven hard while it still points the wrong way |
| Steering rate limited to 1 rad/s | Keeps steering realistic and stable |
| Steering links of 1 kg, inertia 0.05 | Small but non-zero mass keeps the joint chain well conditioned; the mass is subtracted from the chassis, so the total stays 1025 kg |
| Aerodynamic drag `−½ρC_dA·|v|·v` each step | Included for completeness; at Mars air density it is tiny |
| `pose()` reports the ground point under the body | What the navigator, M5 and the renderer all need |
| Inactive wheeled rover is **parked** with collisions off | Otherwise, while the kinematic model is active, it would remain in the world as an invisible obstacle for rocks and M5's lidar |

A **kinematic** model (speed and turn rate with a 0.5 s lag, pitch and roll from terrain samples at
the chassis ends and sides) is the fallback when PyBullet is unavailable. Switching models at run
time keeps position, heading and speed.

**Evidence.** On flat ground the wheeled rover settles with no tilt and holds 1.000 m/s; it turns in
place without drifting. On hilly terrain at 2 m/s and 0.1 rad/s for 20 s, its heading changed 113.7°
against an ideal 114.6°, and it stayed within 8 mm of the terrain surface.

## Imagery

Textures come from NASA Mars Trek tiles, which use the same equirectangular projection as the
terrain rasters, so a texture is a plain lon/lat crop with no reprojection.
- The context uses CTX (6 m). The corridor uses CTX with HiRISE (25 cm) on top, after matching
  HiRISE's mean and spread per colour channel to CTX where both exist, so the footprint shows no seam.
- The zoom is the highest that keeps the texture within 4096 px.
- Tiles are fetched with 16 threads and cached on disk; finished textures are written atomically
  under a per-file lock, because the background warm-up and browser requests can race.
- The service fetches one tile first and gives up at once if the provider is unreachable, then stops
  retrying for 60 s. Without this, an offline machine waited about 2 s per tile.

Measured on the demo route: the context texture (2096 × 990) builds in about 4 s and the corridor
(3605 × 1429) in about 9 s on first use, then comes from the cache.

## Known limitations

- **Mission build pauses telemetry for about 3 s.** Building the PyBullet heightfield for the
  1834 × 727 demo corridor runs on the event-loop thread, because PyBullet is not thread-safe.
- **First corridor texture takes about 9 s.** The page shows the plain base colour until it arrives.
- **Flat-plane frame.** Curvature is ignored over the roughly 9 km corridor, as agreed in C1.
- **The NASA model has no separate wheels**, so wheel steer and spin are only animated on the
  stand-in rover.
- **Prototype speeds.** Speeds above Perseverance's real 0.042 m/s and the time warp are demo
  accelerations, and the page says so.
