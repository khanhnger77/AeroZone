# M5 · Obstacle avoidance

Optional random boulders and craters on the planned route, a virtual 10 m 360° lidar, hazard
detection, and a local navigator that detours around what the lidar finds and rejoins the route.
M4 talks to this package only through `ObstacleAvoidance` (contract C8); everything else is internal.

> The boulders and craters are **synthetic test obstacles**: the 5 m orbital DTM cannot resolve
> rover-scale rocks, so the demo drops a few large ones on the route to exercise the lidar and the
> navigator. They are labelled as such in the UI.

## Roles

| File | What it does |
|---|---|
| `service.py` | **C8 facade** `ObstacleAvoidance`: `setup / reset / describe / tick / command / status / stream_due / encode_frame`. Keeps the orbital prior, schedules scans, builds the C6 `avoid` telemetry and the LDR1 frame |
| `obstacles.py` | Boulders: sparse sites along the route, convex-hull rock meshes (outward faces), surface samples for the lidar, static PyBullet `GEOM_MESH` colliders |
| `craters.py` | Craters carved **into the shared corridor grid** (bowl + ejecta rim), so collider, mesh and lidar agree |
| `lidar.py` | `VirtualLidar`: 240 × 32 image, 1.5° × 1.875°, −45…+15°, 10 m; z-buffered point splatting |
| `planner.py` | `ObstacleMap` (hazard cells), `classify_parts`, `shadow_hazards`, `LocalMap` clearance field, `ArcPlanner` footprint model, **A\*** + path smoothing, `AvoidanceSupervisor` |
| `geometry.py` | Bilinear heights (same rule as `Grid.height_at`), quaternions, `Route` arc-length helper |
| `web/obstacles_view.js` | C8 browser module: `rocksGroup`, `cratersGroup`, `ArcView` (Three.js r170) |
| `config.yaml` | C10, verbatim |

## Algorithm

**Sensing.** The terrain under the rover is sampled on a polar stencil (0.75° × 0.1 m, out to 10 m);
rock surfaces contribute their area-weighted samples, splatted over the pixels their surface patch
subtends so rocks are solid occluders. Every candidate is projected into the sensor image and a
z-buffer keeps the nearest return per pixel. One scan costs ≈ 8 ms.

**Hazards are prior-relative.** The corridor grid *before* craters are carved is the orbital DTM prior.

* *step* (positive): in a 0.5 m grid a cell rises more than 0.30 m above its 3 × 3 neighbourhood
  minimum of the residual `z − prior`;
* *hole* (negative): more than 0.30 m below the prior;
* *range shadow* (negative): downward rays (±110° forward) are traced against the prior; a ray that
  comes back long or empty where the prior predicts a hit marks a drop-off at the expected hit point.

Working on the residual means a plain slope, however steep, never produces a hazard.

**Map.** Hazard cells live in two world-fixed sets (0.2 m keys). Each scan rasterises an 80 m window,
grows the negative set by 1.5 m, and takes a distance transform → a clearance field. The rover
footprint (2.9 × 2.7 m) is three circles (r 1.45 m) along the heading; a pose is clear when the minimum
clearance at the three centres exceeds 1.80 m (radius + 0.35 m margin).

**Supervisor** (`track → avoid → (recover) → track`, or `blocked`):

1. *Track*: the M4 navigator's command passes through. A route point within 12 m in the keep-out, or
   the follower's 6 m arc touching it, switches to *avoid*.
2. *Avoid*: goals are route points 8–45 m ahead with clearance > 2.1 m. **A\*** on a 0.4 m 8-connected
   grid finds the path: cost `1 + 4·max(0, 3 − clearance)/3` per metre, keep-out impassable,
   previously failed paths +30. The heuristic is the Euclidean distance to the nearest goal (one
   distance transform); every cell costs ≥ 1 per metre, so it is admissible and consistent and A\*
   returns the optimal path (a test checks it against Dijkstra).
3. *Smoothing*: the grid path is string-pulled (a shortcut never passes closer to an obstacle than the
   cells it replaces), its corners are rounded (Chaikin, each cut checked against the full footprint
   clearance), and it starts along the rover's heading when that is clear. Once a detour has picked a
   side of the route, cells on the other side cost +3, so a partly-seen obstacle cannot make the plan
   flip left-right between scans. While avoiding, acceleration (0.4 m/s²) and turn-rate changes
   (0.6 rad/s²) are rate-limited; braking is never delayed.
4. *Pure pursuit* on the path (look-ahead 2.5 m, ≤ 0.5 m/s, 0.3 m/s near the margin).
5. **Safety check on every 50 Hz command**: the next 2 m of the commanded arc must keep ≥ 1.60–1.80 m
   clearance, otherwise turn in place (if the swing is clear), back up, or hold.
6. *Rejoin* when the route ahead is clear, cross-track < 0.6 m and heading within 25°.
7. *Watchdog*: no path or no progress for 60 s → *recover* (reverse along the trail, penalise the
   failed path), up to 4 times, then *blocked*.

Constants added for smoothing (`SIDE_SWITCH_COST`, `LEAD_IN_M`, `ACCEL_M_S2`, `YAW_ACCEL_RAD_S2`) live in
`planner.py` because `config.yaml` is contract C10 and must stay verbatim; moving them into the config
is a proposed contract change.

## Wire format (C6 LDR1, produced by `encode_frame`)

```
"LDR1" · uint32 LE header_len · header JSON (padded with spaces to ×4)
int16 LE [n·3] points, sensor frame (x fwd, y left, z up), cm
uint8     [n]  1 = hazard, 0 = ground
uint16 LE [h·w] depth, cm, 0 = no return; row 0 = lowest elevation; col c = −180° + (c + 0.5)·360°/w
```

Header: `n, w, h, range, v_fov, sim_time, mode, route, candidates, carrot, shadows, rocks_near,
craters_near` (body-frame metres). `service.decode_frame` is the reference reader.

## Tests

`python -m pytest -q sim/obstacle_avoid/tests` (25 tests, ≈ 3 min; no M4 needed: `tests/stubs.py`
rebuilds the C8 `Grid`, `Pose`, navigator and two rover stand-ins, and uses PyBullet DIRECT to measure
true body-to-rock gaps).

* boulder sites (sparse, min spacing, on the route), rock hulls (outward faces);
* lidar shape / range, occlusion behind a rock, rock vs ground classification;
* craters: profile at grid posts, generator keep-out, drop-off detection inside the rim, **no false
  hazards on plain, tilted and rolling slopes**;
* A\* = Dijkstra optimal cost; LDR1 round trip; disabled mode is a pass-through;
* closed loop (0 contacts, back on the route, final mode `track`): single rock (both models), two rocks
  4 m apart, big boulders single and paired, **double boulder at 2 m/s and 5×**, **wedged start**,
  crater detours without crossing the rim.

## Campaign

`python -m sim.obstacle_avoid.tests.campaign --seeds 1-48 --model wheeled --speed 2 --warp 5 --jobs 8`

On a checkout with M1 data, M2 and M4 (the integration branch) it drives **M2's recommended route from
the landing site to Cheyava Falls** on **M4's corridor** with **M4's PyBullet 6-wheel rover and
navigator**, in the C8 build and call order; otherwise it falls back to a labelled synthetic stand-in.
To run this branch's M5 against another checkout set `M5_REPO_ROOT`.

`--segments` (mode A) drives only the stretches around each obstacle group (≈ 37 % of the route, spawned
25 m before the first obstacle and ending 25 m after the last), ≈ 2.7× faster than the full drive with
the same terrain, obstacles, rover and planner.

**Results:** not recorded yet; the 48-seed real-route run (mode A and full drive) is in progress and the
table will be added here.

## Simplifications

* The prior used for classification *is* the terrain the lidar sees outside craters, so there is no
  DTM error or sensor noise yet; on real data both would add false positives to tune against.
* Obstacles are synthetic and sparse; real rock-abundance maps would replace the generator.
* Detour costs use lidar clearance only, not slope: a detour can climb a slope the orbital planner
  avoided (≤ ~15 m off the route).
* A\* is pure Python (≈ 20 ms per plan, explores ~10 % of the window); fine at 5 scans/s.
