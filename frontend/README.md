# MARS ODYSSEY — M3 planner web app

React 19, TypeScript, Vite 6, Leaflet and Tailwind v4. M3 consumes the C3–C7 contracts in the team's `M3_web_app.md`; it does not implement the terrain pipeline, route search, simulation physics or obstacle avoidance.

Run from this directory with Node 20:

```sh
npm install
npm run dev
npm run test
npm run lint
npm run build
npm run preview
```

Development uses port 5173; production preview uses 4173. Both proxy `/api` to localhost:8000 and `/sim` plus `/.sim` to localhost:8100, including WebSockets. The browser uses the web app's origin for every backend connection. NASA map tiles are fetched directly from Mars Trek.

## Demo integration

Start M2 on 8000 and M4 + M5 on 8100. In Mission Planner, compute routes (or use the landing → Cheyava Falls scenario), select an alternative, optionally enable obstacles, and choose Run demo. The app posts the selected geometry, alternatives, endpoint coordinates and obstacle seed to `/sim/api/mission`. On the iframe's authenticated same-origin `ready` message, it sets vehicle speed, sets warp and starts driving, in order.

The same Leaflet instance becomes a 200 × 110 tracking inset. Telemetry updates the rover arrow and controls. With obstacles enabled, LidarScreen separately subscribes to `lidar` on `/sim/ws`, reads binary LDR1 frames and offers Cloud / Depth views. Exit pauses the sim and restores the full map. Command failures appear in the Mission panel.

`npm test` checks unaligned LDR1 blocks, corrupt/truncated frames, mission request serialization and sim error responses. It does not require M2/M4/M5. The tests use Node's test runner and the existing TypeScript dependency.

## Ownership and remaining integration checks

- M3 owns UI code in `frontend/`. **M1 generates `frontend/public/data/` under C3.** Preserve that directory when merging M3 and arrange with M1 before regenerating it.
- Root `pixi.toml`, launcher, data generation and documentation belong to M1. They were not added by M3. Until M1 lands, the existing root README/Makefile describe the older launcher.
- M4 must send `ready` with the mission id and C7 telemetry from the embedded iframe, and support POST commands. Both origin and iframe source are checked by M3.
- M5 must supply C6 LDR1, including centimetre units, little-endian blocks and the documented body-frame coordinates. The lidar socket subscribes only to `["lidar"]` and reconnects every 1.5 seconds.
- Full acceptance still requires the actual M2/M4/M5 services: drive at 2 m/s and 5×, observe a detour and rejoin, verify real obstacle markers and lidar depth, and check the public tunnel. A stub passing cannot certify physics or avoidance.

Validation during this implementation: TypeScript + production build passed; ESLint has no errors (three existing Fast Refresh export warnings in MarsMap). Chrome tested the desktop planner/demo flow against local C4/C5/C6/C7 stubs, including startup command order, telemetry, synthetic binary lidar through the Vite preview WebSocket proxy, speed commands, pause/exit, map instance preservation and `/.sim` rewriting. Actual M4/M5 were absent from the checkout.

## Point explorer: measured terrain and rover panoramas

On desktop, click the map or a sample marker to open a place card docked directly to the left of the Locator at the bottom right. The map keeps a selection marker; cursor updates do not move or recreate the card. Terrain 3D opens a rotatable, zoomable 160 × 160 m local mesh using `/api/point`: 17 × 17 samples, 10 m ground spacing, at most eight concurrent requests. It uses actual Mars-radius coordinates and MOLA-referenced elevations from the existing 5 m analysis grid. Missing heights leave holes; an outside-grid point does not get a synthetic surface. Closing the view, changing points or switching tabs cancels outstanding sampling. Up to eight patches are cached per session. Drag/scroll or use arrow keys and +/−; 0 resets the camera.

360° markers open the panorama tab. Three natural-colour cylindrical previews are bundled under `public/panoramas/`: Butler Landing, Van Zyl Overlook and Bright Angel. These are published stitched images, not generated images or arbitrary raw photographs wrapped into a sphere. Pan horizontally through 360°; zoom to inspect details. Vertical coverage stays limited to the original photo. Panorama viewing needs no Planner or rover sim connection.

The photo catalogue shows nearby panoramas only within 500 m, with distance from the selected point and an explicit approximate-location note. Marker coordinates use the local MMGIS snapshot's sol-13 fix for the landing site, sol-52 fix for Van Zyl and sol-1177 fix for Bright Angel. They are nearby rover fixes, not surveyed camera positions. Van Zyl's separate marker appears at high zoom to avoid covering the landing marker; both appear in the nearby-photo selector. The empty panorama view offers the three photographed sites.

Images: [Mastcam-Z panorama collection](https://mastcamz.asu.edu/mastcam-zs-360-panorama-collection/); [NASA/JPL Bright Angel description](https://www.jpl.nasa.gov/images/pia26369-perseverance-captures-bright-angel-in-360-degrees/). Credit: **NASA/JPL-Caltech/ASU/MSSS**. Image URLs, byte counts and SHA-256 are recorded in `public/panoramas/sources.json`. The three preview images total about 640 KiB; full-resolution source images remain linked from the viewer.

This point explorer is independent of the M4 driving simulation. The header's **Rover sim** status refers to Run demo; it does not indicate whether local Terrain 3D or stored panoramas are available.

The Locator is linked to the main map: its cyan rectangle tracks the current viewport, its orange outline marks the study area, and its coordinates follow the view centre. Drag or click the Locator to recenter the main map, or scroll to zoom. Updates from the main map are separated from user navigation to prevent a feedback loop.
