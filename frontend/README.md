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
