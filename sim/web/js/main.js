// Sim page entry point: loads the mission, builds the Three.js world and renders server telemetry.
//
// The page is a view, not a second simulator (principle 1). Load order: /config -> /api/mission ->
// both terrain grids -> world -> rover -> post `ready` to the planner (C7). Telemetry goes into a
// ~220 ms interpolation buffer (principle 7); the HUD and the parent window get ~10 Hz updates.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { getJSON, postJSON, SimSocket, InterpBuffer } from './net.js';
import { loadGrid, terrainMesh, ribbon, marker, disposeTree } from './terrain.js';
import { RoverView } from './rover.js';
import { AstronautView } from './astronaut.js';

const params = new URLSearchParams(location.search);
const EMBED = params.get('embed') === '1';
const IN_FRAME = window.parent !== window;
if (EMBED) document.body.classList.add('embed');

const $ = (id) => document.getElementById(id);
const POST_HZ = 10;
const STALE_MS = 1500;
const CHASE = { back: 11, up: 5.5, tau: 0.25, minClear: 2.0 };
const CHASE_ON_FOOT = { back: 5.5, up: 2.6, tau: 0.25, minClear: 1.2 };   // astronaut model

// ------------------------------------------------------------------------------- parent window (C7)
function post(type, payload = {}) {
  if (IN_FRAME) window.parent.postMessage({ source: 'mo-sim', type, ...payload }, location.origin);
}

// ------------------------------------------------------------------------------- UI helpers
const loadingEl = $('loading');
function step(name, state) {
  const li = document.querySelector(`#steps li[data-step="${name}"]`);
  if (li) li.className = state;
}
function resetSteps() {
  document.querySelectorAll('#steps li').forEach((li) => { li.className = ''; });
  $('load-error').textContent = '';
}

let toastTimer = 0;
function toast(text, kind = '') {
  const el = $('toast');
  el.textContent = text;
  el.className = `panel on ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'panel'; }, 4000);
}

const fmt = {
  dist: (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m.toFixed(0)} m`),
  time: (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  },
};

// ------------------------------------------------------------------------------- renderer & scene
const renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.outputColorSpace = THREE.SRGBColorSpace;
$('view').appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 60000);
camera.position.set(-20, 15, 20);

// Butterscotch sky dome that follows the camera (no fog: the whole AOI stays visible).
const sky = new THREE.Mesh(
  new THREE.SphereGeometry(50000, 32, 16),
  new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    uniforms: { top: { value: new THREE.Color(0xb98a5e) }, horizon: { value: new THREE.Color(0xe9c69a) },
                below: { value: new THREE.Color(0x8a6244) } },
    vertexShader: `varying vec3 vDir;
      void main() { vDir = normalize(position);
        vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_Position = p.xyww; }`,
    fragmentShader: `uniform vec3 top; uniform vec3 horizon; uniform vec3 below; varying vec3 vDir;
      void main() { float h = vDir.y;
        vec3 c = h > 0.0 ? mix(horizon, top, pow(h, 0.6)) : mix(horizon, below, min(-h * 4.0, 1.0));
        gl_FragColor = vec4(c, 1.0); }`,
  }));
sky.renderOrder = -1;
sky.frustumCulled = false;
scene.add(sky);

scene.add(new THREE.HemisphereLight(0xffe2c0, 0x6b4a33, 0.9));
const sun = new THREE.DirectionalLight(0xfff0dd, 2.2);
const SUN_DIR = new THREE.Vector3(-0.55, 0.75, 0.45).normalize();   // from the south-west
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -40, right: 40, top: 40, bottom: -40, near: 1, far: 400 });
sun.shadow.bias = -0.0005;
sun.shadow.normalBias = 0.05;
scene.add(sun, sun.target);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.495;
controls.enabled = false;

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// ------------------------------------------------------------------------------- state
let config = null;
let mission = null;
let grids = null;             // { corridor, context }
let world = null;             // THREE.Group with everything mission-specific
let rover = null;
let astro = null;             // AstronautView, shown when telemetry.model === 'astronaut'
let arcView = null;
let obstacleGroups = [];      // boulders + crater markers: hidden in the whole-AOI view
let loading = null;           // the single in-flight loadMission() promise
let loadingId = null;         // mission id that load is building
let serverMission = null;     // newest mission id the server announced (telemetry / mission_loaded)
let latest = null;            // newest telemetry
let lastPost = 0;
let cameraMode = 'chase';
let haveCameraFix = false;
const buffer = new InterpBuffer(220);

function sampler(x, y) {
  if (!grids) return 0;
  return grids.corridor.contains(x, y) ? grids.corridor.heightAt(x, y) : grids.context.heightAt(x, y);
}

// ------------------------------------------------------------------------------- loading
async function loadMission() {
  if (loading) return loading;
  loading = (async () => {
    resetSteps();
    loadingEl.classList.remove('hidden');
    let current = 'config';
    let stale = false;
    try {
      step('config', 'run');
      if (!config) config = await getJSON('config');
      step('config', 'done');

      current = 'mission'; step('mission', 'run');
      const m = await getJSON('api/mission');
      loadingId = m.id;
      step('mission', 'done');

      current = 'terrain'; step('terrain', 'run');
      const [corridor, context] = await Promise.all([
        loadGrid('corridor', m.terrain.corridor, m.id),
        loadGrid('context', m.terrain.context, m.id),
      ]);
      step('terrain', 'done');

      current = 'world'; step('world', 'run');
      await nextFrame();
      buildWorld(m, { corridor, context });
      await addObstacles(m);
      step('world', 'done');

      current = 'rover'; step('rover', 'run');
      if (!rover) {
        rover = new RoverView(config.rover);
        const ok = await rover.load(config.rover.model_url);
        if (!ok) toast('NASA rover model unavailable: showing a stand-in', 'warn');
      }
      scene.add(rover.root);
      if (config.astronaut && !astro) astro = new AstronautView(config.astronaut);
      if (astro) scene.add(astro.root);
      step('rover', 'done');

      buffer.clear();
      haveCameraFix = false;
      if (m.kind === 'world') cameraMode = 'overview';   // the world demo opens on the whole AOI
      setupControls();
      loadingEl.classList.add('hidden');
      post('ready', { mission: m.id });
    } catch (err) {
      if (err.status === 409) {
        // The server switched missions while we were fetching: not a failure, just start over.
        stale = true;
      } else {
        console.error(err);
        step(current, 'fail');
        $('load-error').textContent = String(err.message || err);
        post('error', { message: String(err.message || err) });
      }
    } finally {
      // Reload once more if this load went stale, or a newer mission was announced meanwhile.
      const again = stale || (serverMission !== null && loadingId !== null && serverMission !== loadingId);
      loading = null;
      loadingId = null;
      if (again) loadMission();
    }
  })();
  return loading;
}

/** Server announced mission `id`: load it now, or right after the load in flight. */
function noteServerMission(id) {
  if (!id) return;
  serverMission = id;
  if (!loading && (!mission || id !== mission.id)) loadMission();
}

/**
 * Yield one frame before the heavy mesh build. requestAnimationFrame never fires in a hidden tab
 * or iframe, so a timeout keeps the load from stalling there.
 */
function nextFrame() {
  return new Promise((r) => {
    const done = () => { clearTimeout(timer); r(); };
    const timer = setTimeout(done, 50);
    requestAnimationFrame(done);
  });
}

function buildWorld(m, g) {
  if (world) { scene.remove(world); disposeTree(world); }
  if (arcView) arcView = null;
  mission = m;
  grids = g;
  world = new THREE.Group();
  world.name = 'world';

  const c = g.corridor;
  // Context everywhere except one context cell inside the corridor edge (no cracks at the seam).
  const hole = { xMin: c.x0 + g.context.dx, xMax: c.xMax - g.context.dx,
                 yMin: c.y0 + g.context.dy, yMax: c.yMax - g.context.dy };
  const ctxMesh = terrainMesh(g.context, {
    textureUrl: `api/texture/context.jpg?m=${m.id}`, renderer, hole,
    onTexture: (ok) => { if (!ok) toast('Context imagery unavailable: plain terrain shown', 'warn'); },
  });
  ctxMesh.position.y = -0.3;           // the overlap band sits just under the corridor surface
  const corMesh = terrainMesh(c, {
    textureUrl: `api/texture/corridor.jpg?m=${m.id}`, renderer,
    onTexture: (ok) => { if (!ok) toast('Corridor imagery unavailable: plain terrain shown', 'warn'); },
  });
  world.add(ctxMesh, corMesh);

  for (const alt of m.alternatives || []) {
    world.add(ribbon(alt.xy, sampler, { width: 1.0, color: alt.color, opacity: 0.45, lift: 0.12 }));
  }
  if (m.route.xy.length >= 2) {           // the world demo has no route
    world.add(ribbon(m.route.xy, sampler, { width: 1.6, color: m.route.color, opacity: 0.9, lift: 0.15 }));
    const [sx, sy] = m.route.xy[0];
    const [ex, ey] = m.route.xy[m.route.xy.length - 1];
    world.add(marker(sx, sy, sampler, '#ffffff'));
    world.add(marker(ex, ey, sampler, m.route.color, { pole: true }));
  }
  worldState = m.world ? { mission: m.id, ...m.world } : null;
  for (const [, s] of squares) { agentsGroup.remove(s.sprite); s.sprite.material.map.dispose(); s.sprite.material.dispose(); }
  squares.clear();
  scene.add(world);
  updateSquares();
}

async function addObstacles(m) {
  if (!m.obstacles || !m.obstacles.enabled) return;
  try {
    const mod = await import('../obstacle_avoid/obstacles_view.js');
    obstacleGroups = [mod.rocksGroup(m.obstacles.rocks || [], sampler), mod.cratersGroup(m.obstacles.craters || [], sampler)];
    world.add(...obstacleGroups);
    arcView = new mod.ArcView(sampler);
    world.add(arcView.line);
  } catch (err) {
    console.warn('obstacle view unavailable', err);
    toast('Obstacle view unavailable', 'warn');
  }
}

// ------------------------------------------------------------------------------- cameras
const tmp = new THREE.Vector3();
const roverPos = new THREE.Vector3();
const lastRoverPos = new THREE.Vector3();

function setupControls() {
  setCamera(cameraMode, true);
}

function overviewPose() {
  const g = grids.context;
  const cx = (g.x0 + g.xMax) / 2, cy = (g.y0 + g.yMax) / 2;
  const span = Math.max(g.xMax - g.x0, g.yMax - g.y0);
  const dist = (span / 2) / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
  return { target: new THREE.Vector3(cx, sampler(cx, cy), -cy),
           position: new THREE.Vector3(cx, sampler(cx, cy) + dist * 0.82, -cy + dist * 0.57) };
}

function setCamera(mode, force = false, agent = null) {
  if (!['chase', 'orbit', 'overview'].includes(mode)) return;
  if (mode === cameraMode && !force && !agent) return;
  cameraMode = mode;
  post('camera', { mode });
  if (mode !== 'overview' && !force) takeControl(agent);
  if (agent) haveCameraFix = false;
  document.querySelectorAll('[data-cam]').forEach((b) => b.classList.toggle('active', b.dataset.cam === mode));
  controls.enabled = mode !== 'chase';
  if (!grids) return;
  if (mode === 'overview') {
    const p = overviewPose();
    camera.position.copy(p.position);
    controls.target.copy(p.target);
    controls.maxDistance = Infinity;
  } else if (mode === 'orbit') {
    controls.target.copy(roverPos);
    camera.position.copy(roverPos).add(tmp.set(-14, 9, 14));
    controls.maxDistance = 3000;
  } else {
    haveCameraFix = false;
  }
  lastRoverPos.copy(roverPos);
  controls.update();
}

function updateCamera(dt) {
  if (cameraMode === 'chase') {
    const chase = onFoot() ? CHASE_ON_FOOT : CHASE;
    const fwd = tmp.set(1, 0, 0).applyQuaternion(activeBody().root.quaternion);
    fwd.y = 0;
    if (fwd.lengthSq() < 1e-6) fwd.set(1, 0, 0);
    fwd.normalize();
    const desired = roverPos.clone().addScaledVector(fwd, -chase.back);
    desired.y += chase.up;
    const ground = sampler(desired.x, -desired.z) + chase.minClear;
    if (desired.y < ground) desired.y = ground;
    if (!haveCameraFix) { camera.position.copy(desired); haveCameraFix = true; }
    else camera.position.lerp(desired, 1 - Math.exp(-dt / chase.tau));
    camera.lookAt(roverPos.x, roverPos.y + (onFoot() ? 1.1 : 1.2), roverPos.z);
  } else if (cameraMode === 'orbit') {
    // Follow the rover: move camera and target by the rover's displacement.
    tmp.subVectors(roverPos, lastRoverPos);
    camera.position.add(tmp);
    controls.target.add(tmp);
    controls.update();
  } else {
    controls.update();
  }
  lastRoverPos.copy(roverPos);
}

// ------------------------------------------------------------------------------- HUD
function updateHud(t) {
  const n = t.nav || {};
  $('t-state').textContent = n.state || '–';
  $('t-speed').textContent = `${(t.speed_m_s ?? 0).toFixed(2)} m/s`;
  $('t-dist').textContent = `${fmt.dist(n.distance_done_m || 0)} / ${fmt.dist(n.distance_remaining_m || 0)}`;
  $('t-elev').textContent = `${(t.elev_m ?? 0).toFixed(1)} m`;
  $('t-att').textContent = `${(t.pitch_deg ?? 0).toFixed(1)}° · ${(t.roll_deg ?? 0).toFixed(1)}°`;
  $('t-time').textContent = `${fmt.time(t.sim_time || 0)}${t.warp && t.warp !== 1 ? ` ×${t.warp}` : ''}`;
  $('progress').firstElementChild.style.width = `${Math.min(100, (n.progress || 0) * 100).toFixed(1)}%`;
  const start = $('btn-start');
  start.textContent = n.state === 'driving' ? 'Pause' : 'Start';
  start.disabled = n.state === 'arrived';
  syncSelect('sel-warp', t.warp);
  syncSelect('sel-speed', n.cruise_m_s);
}

function syncSelect(id, value) {
  const el = $(id);
  if (value === undefined || value === null || document.activeElement === el) return;
  const v = String(value);
  if ([...el.options].some((o) => o.value === v)) el.value = v;
}

function fillControls() {
  const c = config.controls;
  $('sel-speed').innerHTML = c.speeds_m_s.map((v) =>
    `<option value="${v}">${v === c.real_max_speed_m_s ? `${v} m/s (real)` : `${v} m/s`}</option>`).join('');
  $('sel-warp').innerHTML = c.warps.map((w) => `<option value="${w}">${w}× time</option>`).join('');
  $('sel-speed').value = String(c.default_speed_m_s);
}

async function command(cmd) {
  try {
    const r = await postJSON('api/command', cmd);
    if (!r.ok) toast(r.error || 'command failed', 'warn');
  } catch (err) {
    toast(String(err.message || err), 'bad');
  }
}

$('btn-start').addEventListener('click', () =>
  command({ type: latest && latest.nav && latest.nav.state === 'driving' ? 'pause' : 'start' }));
$('btn-reset').addEventListener('click', () => command({ type: 'reset' }));
$('sel-speed').addEventListener('change', (e) => command({ type: 'set_speed', value: Number(e.target.value) }));
$('sel-warp').addEventListener('change', (e) => command({ type: 'set_warp', value: Number(e.target.value) }));
// The object is chosen before the run (planner's Mission tab); it cannot be switched mid-simulation.
document.querySelectorAll('[data-cam]').forEach((b) => b.addEventListener('click', () => setCamera(b.dataset.cam)));

// ------------------------------------------------------------------------------- world objects
// World messages (~5 Hz) list every object. In the whole-AOI view each one is a cheap square
// sprite with its name; in the chase / orbit views only the selected object's model is drawn and
// the others are tracked (by the server) but not rendered.
const KIND_COLOR = { rover: '#5ee6ff', astronaut: '#fbbf24' };
let worldState = null;
const agentsGroup = new THREE.Group();
agentsGroup.name = 'agents';
scene.add(agentsGroup);
const squares = new Map();     // id -> { sprite, key }

function squareTexture(name, kind, selected) {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 96;
  const g = c.getContext('2d');
  g.font = 'bold 26px system-ui, sans-serif';
  g.textAlign = 'center';
  g.lineWidth = 6; g.strokeStyle = 'rgba(0,0,0,.85)'; g.strokeText(name, 128, 30);
  g.fillStyle = '#ffffff'; g.fillText(name, 128, 30);
  g.fillStyle = KIND_COLOR[kind] || '#ffffff';
  g.fillRect(110, 50, 36, 36);
  g.lineWidth = selected ? 5 : 3; g.strokeStyle = selected ? '#ffffff' : '#05070a';
  g.strokeRect(110, 50, 36, 36);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function updateSquares() {
  if (!worldState) return;
  const seen = new Set();
  for (const a of worldState.agents) {
    seen.add(a.id);
    const key = `${a.name}|${a.kind}|${a.id === worldState.selected}`;
    let s = squares.get(a.id);
    if (!s || s.key !== key) {
      if (s) { agentsGroup.remove(s.sprite); s.sprite.material.map.dispose(); s.sprite.material.dispose(); }
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: squareTexture(a.name, a.kind, a.id === worldState.selected),
        sizeAttenuation: false, depthTest: false, transparent: true }));
      sprite.center.set(0.5, 0.0);
      sprite.scale.set(0.16, 0.06, 1);
      sprite.renderOrder = 10;
      agentsGroup.add(sprite);
      s = { sprite, key };
      squares.set(a.id, s);
    }
    s.sprite.position.set(a.x, a.z + 1, -a.y);
  }
  for (const [id, s] of squares) {
    if (!seen.has(id)) { agentsGroup.remove(s.sprite); s.sprite.material.map.dispose(); s.sprite.material.dispose(); squares.delete(id); }
  }
}

function isWorld() { return !!(mission && mission.kind === 'world'); }

/** Riding along with a world object selects it; it keeps wandering or its mission until driven. */
function takeControl(agent) {
  const id = agent || (worldState && worldState.selected);
  if (isWorld() && id) {
    if (worldState) worldState.selected = id;
    buffer.clear();                       // the new object's pose replaces the old one's
    const cmd = { type: 'select', agent: id };   // keeps its mission; WASD / joystick take over
    if (!socket.send(cmd)) command(cmd);
  }
}

// WASD / arrow keys drive the selected object (sent at 10 Hz while held, zero once on release).
const keys = new Set();
const KEYMAP = { KeyW: 'f', ArrowUp: 'f', KeyS: 'b', ArrowDown: 'b', KeyA: 'l', ArrowLeft: 'l', KeyD: 'r', ArrowRight: 'r' };
let driveTimer = 0;
function driveInput() {
  return { v: (keys.has('f') ? 1 : 0) - (keys.has('b') ? 1 : 0), w: (keys.has('l') ? 1 : 0) - (keys.has('r') ? 1 : 0) };
}
function sendDrive() {
  const cmd = { type: 'drive', ...driveInput() };
  if (!socket.send(cmd)) command(cmd);
}
function onKey(e, down) {
  const k = KEYMAP[e.code];
  if (!k || !isWorld() || cameraMode === 'overview' || e.target instanceof HTMLSelectElement) return;
  e.preventDefault();
  if (down) keys.add(k); else keys.delete(k);
  sendDrive();
  clearInterval(driveTimer);
  if (keys.size) driveTimer = setInterval(sendDrive, 100);
}
window.addEventListener('keydown', (e) => { if (!e.repeat) onKey(e, true); });
window.addEventListener('keyup', (e) => onKey(e, false));
window.addEventListener('blur', () => { if (keys.size) { keys.clear(); clearInterval(driveTimer); sendDrive(); } });

// ------------------------------------------------------------------------------- socket
const socket = new SimSocket('ws');

socket.on('hello', (msg) => {
  config = msg.config;
  if (!$('sel-speed').options.length) fillControls();
});

socket.on('telemetry', (t) => {
  if (!mission || t.mission !== mission.id) {
    noteServerMission(t.mission);
    return;
  }
  if (isWorld() && worldState && t.agent && t.agent !== worldState.selected) return;   // pre-switch frame
  buffer.push(t);
  latest = t;
  if (arcView && t.avoid !== undefined) arcView.update(t.avoid);
  const now = performance.now();
  if (now - lastPost >= 1000 / POST_HZ) {
    lastPost = now;
    updateHud(t);
    let avoid = t.avoid;
    if (avoid && avoid.arc !== undefined) { avoid = { ...avoid }; delete avoid.arc; }
    post('telemetry', { lon: t.lon, lat: t.lat, yaw_deg: t.yaw_deg, speed_m_s: t.speed_m_s, nav: t.nav,
                        warp: t.warp, model: t.model, sim_time: t.sim_time, pitch_deg: t.pitch_deg,
                        roll_deg: t.roll_deg, tilt_deg: t.tilt_deg, elev_m: t.elev_m, avoid });
  }
});

socket.on('mission_loaded', (msg) => noteServerMission(msg.mission));

socket.on('world', (msg) => {
  if (!mission || msg.mission !== mission.id) return;
  worldState = msg;
  updateSquares();
});

socket.on('world_event', (ev) => {
  toast(ev.detail || ev.event);
  const { type, ...rest } = ev;
  post('event', { ...rest, kind: type });
});

for (const kind of ['physics_event', 'avoid_event']) {
  socket.on(kind, (ev) => {
    const text = kind === 'physics_event'
      ? `Physics: ${ev.event}${ev.detail ? ` (${ev.detail})` : ''}`
      : `Avoidance: ${ev.mode}${ev.detail ? ` (${ev.detail})` : ''}`;
    toast(text, kind === 'physics_event' || ev.mode === 'blocked' ? 'warn' : '');
    const { type, ...rest } = ev;
    post('event', { ...rest, kind: type });   // keep type "event" (C7); `kind` says which
  });
}

// Planner -> sim messages (C7).
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin) return;
  const d = e.data;
  if (!d || d.source !== 'mo-hub') return;
  if (d.type === 'command' && d.command && typeof d.command === 'object') {
    if (!socket.send(d.command)) command(d.command);
  } else if (d.type === 'locations' && Array.isArray(d.list)) {
    userLocations = d.list.filter((l) => l && Number.isFinite(l.lon) && Number.isFinite(l.lat));
  } else if (d.type === 'camera') {
    setCamera(d.mode, false, typeof d.agent === 'string' ? d.agent : null);
  }
});

// The figure that carries the pose: the astronaut when the "astronaut" motion model is active.
function onFoot() { return !!(astro && latest && latest.model === 'astronaut'); }
function activeBody() { return onFoot() ? astro : rover; }

// ------------------------------------------------------------------------------- near objects + guides
// Chase / orbit views: objects within NEAR_M of the selected vehicle are drawn with their models
// (positions from the 5 Hz world messages, smoothed per frame); the rest stay hidden.
const NEAR_M = 100;
const nearViews = new Map();      // id -> { view, kind, s: {x, y, z, yaw}, phase }
function wrapPi(a) { return Math.atan2(Math.sin(a), Math.cos(a)); }
function updateNearby(dt) {
  const seen = new Set();
  if (worldState && config && cameraMode !== 'overview') {
    const sx = roverPos.x, sy = -roverPos.z;
    for (const a of worldState.agents) {
      if (a.id === worldState.selected || Math.hypot(a.x - sx, a.y - sy) > NEAR_M) continue;
      seen.add(a.id);
      let n = nearViews.get(a.id);
      const yaw = THREE.MathUtils.degToRad(a.yaw_deg);
      if (!n || n.kind !== a.kind) {
        if (n) { scene.remove(n.view.root); disposeTree(n.view.root); }
        const view = a.kind === 'astronaut' && config.astronaut ? new AstronautView(config.astronaut) : new RoverView(config.rover);
        if (view instanceof RoverView) view.load(config.rover.model_url);
        scene.add(view.root);
        n = { view, kind: a.kind, s: { x: a.x, y: a.y, z: a.z, yaw }, phase: 0 };
        nearViews.set(a.id, n);
      }
      const k = 1 - Math.exp(-dt / 0.25);
      n.s.x += (a.x - n.s.x) * k; n.s.y += (a.y - n.s.y) * k; n.s.z += (a.z - n.s.z) * k;
      n.s.yaw += wrapPi(yaw - n.s.yaw) * k;
      n.view.setPose({ x: n.s.x, y: n.s.y, z: n.s.z, q: [0, 0, Math.sin(n.s.yaw / 2), Math.cos(n.s.yaw / 2)] });
      if (n.view instanceof AstronautView) { n.phase += dt * a.speed_m_s * 3.2; n.view.setGait(n.phase, a.speed_m_s); }
    }
  }
  for (const [id, n] of nearViews) {
    if (!seen.has(id)) { scene.remove(n.view.root); disposeTree(n.view.root); nearViews.delete(id); }
  }
}

// Ground guide: a white circle around the selected vehicle and, from it, one arrow per location /
// object type pointing at the nearest one (name + distance), coloured as in the planner legend.
// On a mission (travel / intercept) only the arrow to its target is shown.
const TYPE_COLOR = { HQ: '#f8fafc', Geological: '#a78bfa', Cargo: '#f59e0b', Supplies: '#34d399', Greenhouse: '#a3e635',
                     Attention: '#f87171', rover: '#5ee6ff', astronaut: '#fbbf24' };
let userLocations = [];           // [{name, type, lon, lat}] from the planner (Location tab)
const guide = new THREE.Group();
guide.name = 'guide';
scene.add(guide);
const ring = new THREE.Mesh(new THREE.RingGeometry(0.94, 1, 72),
  new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, depthTest: false, side: THREE.DoubleSide }));
ring.rotation.x = -Math.PI / 2;
ring.renderOrder = 20;
guide.add(ring);
const arrowShape = new THREE.Shape();
arrowShape.moveTo(0, -0.07); arrowShape.lineTo(0.68, -0.07); arrowShape.lineTo(0.68, -0.2);
arrowShape.lineTo(1, 0); arrowShape.lineTo(0.68, 0.2); arrowShape.lineTo(0.68, 0.07); arrowShape.lineTo(0, 0.07);
const arrowGeo = new THREE.ShapeGeometry(arrowShape);
arrowGeo.rotateX(-Math.PI / 2);   // flat on the ground, pointing +x
const arrows = new Map();         // key -> { group, mesh, label, text }
let guideTimer = 0;
let guideTargets = [];

function toLocal(lon, lat) {
  const f = mission.frame, r = THREE.MathUtils.degToRad;
  return { x: f.radius_m * Math.cos(r(f.lat0)) * r(lon - f.lon0), y: f.radius_m * r(lat - f.lat0) };
}
const fmtD = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);

function labelTexture(text, color) {
  const c = document.createElement('canvas');
  c.width = 320; c.height = 56;
  const g = c.getContext('2d');
  g.font = 'bold 26px system-ui, sans-serif';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.lineWidth = 6; g.strokeStyle = 'rgba(0,0,0,.85)'; g.strokeText(text, 160, 28);
  g.fillStyle = color; g.fillText(text, 160, 28);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function computeTargets(sx, sy) {
  const out = [];
  const sel = worldState && worldState.agents.find((a) => a.id === worldState.selected);
  const agents = (worldState ? worldState.agents : []).filter((a) => a.id !== (sel && sel.id));
  const locs = mission && mission.frame ? userLocations.map((l) => ({ ...l, ...toLocal(l.lon, l.lat) })) : [];
  if (sel && sel.mode === 'intercept' && sel.target) {
    const t = agents.find((a) => a.id === sel.target);
    if (t) return [{ key: 'mission', name: t.name, color: TYPE_COLOR[t.kind], x: t.x, y: t.y }];
  }
  if (sel && sel.mode === 'goto' && sel.goal) {
    const g = toLocal(sel.goal.lon, sel.goal.lat);
    const loc = locs.find((l) => Math.hypot(l.x - g.x, l.y - g.y) < 10);
    return [{ key: 'mission', name: loc ? loc.name : 'Destination', color: loc ? TYPE_COLOR[loc.type] : '#ffffff', x: g.x, y: g.y }];
  }
  const nearest = (items, type, color) => {
    let best = null, bd = Infinity;
    for (const it of items) { const d = Math.hypot(it.x - sx, it.y - sy); if (d < bd) { bd = d; best = it; } }
    if (best) out.push({ key: type, name: best.name, color, x: best.x, y: best.y });
  };
  for (const kind of ['rover', 'astronaut']) nearest(agents.filter((a) => a.kind === kind), kind, TYPE_COLOR[kind]);
  for (const type of ['HQ', 'Geological', 'Cargo', 'Supplies', 'Greenhouse', 'Attention']) nearest(locs.filter((l) => l.type === type), type, TYPE_COLOR[type]);
  return out;
}

function updateGuides(dt) {
  const on = !!(grids && mission && cameraMode !== 'overview');
  guide.visible = on;
  if (!on) return;
  const R = onFoot() ? 1.8 : 4.2;                        // circle radius around the vehicle (m)
  const L = onFoot() ? 1.6 : 3.0;                        // arrow length (m)
  guide.position.set(roverPos.x, roverPos.y + 0.15, roverPos.z);
  ring.scale.setScalar(R);
  const sx = roverPos.x, sy = -roverPos.z;
  guideTimer -= dt;
  if (guideTimer <= 0) { guideTimer = 0.25; guideTargets = computeTargets(sx, sy); }
  const keep = new Set();
  for (const t of guideTargets) {
    keep.add(t.key);
    let a = arrows.get(t.key);
    if (!a) {
      const group = new THREE.Group();
      const mesh = new THREE.Mesh(arrowGeo, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.95, depthTest: false, side: THREE.DoubleSide }));
      mesh.renderOrder = 21;
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ depthTest: false, transparent: true, sizeAttenuation: false }));
      label.center.set(0, 0.5);
      label.scale.set(0.21, 0.037, 1);
      label.renderOrder = 22;
      group.add(mesh, label);
      guide.add(group);
      a = { group, mesh, label, text: '' };
      arrows.set(t.key, a);
    }
    const dx = t.x - sx, dy = t.y - sy;
    a.group.rotation.y = Math.atan2(dy, dx);        // +x turned towards (dx, -dy) in scene axes
    a.mesh.position.set(R + 0.15, 0, 0);
    a.mesh.scale.set(L, 1, L * 0.9);
    a.mesh.material.color.set(t.color);
    a.label.position.set(R + L + 0.5, 0.3, 0);
    const text = `${t.name} · ${fmtD(Math.hypot(dx, dy))}`;
    if (text !== a.text) {
      if (a.label.material.map) a.label.material.map.dispose();
      a.label.material.map = labelTexture(text, t.color);
      a.label.material.needsUpdate = true;
      a.text = text;
    }
  }
  for (const [key, a] of arrows) {
    if (!keep.has(key)) {
      guide.remove(a.group);
      a.mesh.material.dispose();
      if (a.label.material.map) a.label.material.map.dispose();
      a.label.material.dispose();
      arrows.delete(key);
    }
  }
}

// ------------------------------------------------------------------------------- frame loop
const clock = new THREE.Clock();
function frame() {
  requestAnimationFrame(frame);
  const dt = Math.min(clock.getDelta(), 0.1);
  if (rover && grids) {
    const s = buffer.sample();
    if (s) {
      const foot = onFoot();
      // World demo, whole-AOI view: squares only (no models). Otherwise only the selected model.
      const squaresOnly = isWorld() && cameraMode === 'overview';
      agentsGroup.visible = squaresOnly;
      for (const g of obstacleGroups) g.visible = cameraMode !== 'overview';
      rover.root.visible = !foot && !squaresOnly;
      if (astro) astro.root.visible = foot && !squaresOnly;
      const body = activeBody();
      body.setPose(s);
      if (foot) astro.setGait(s.wheels && s.wheels[0] ? s.wheels[0].spin : 0, latest ? latest.speed_m_s : 0);
      else rover.setWheels(s.wheels);
      roverPos.copy(body.root.position);
    }
    updateCamera(dt);
    updateNearby(dt);
    updateGuides(dt);
    sun.target.position.copy(roverPos);
    sun.position.copy(roverPos).addScaledVector(SUN_DIR, 200);
    $('stale').classList.toggle('on', buffer.age() > STALE_MS && !!latest);
  }
  sky.position.copy(camera.position);
  renderer.render(scene, camera);
}

if (params.get('debug') === '1') {
  // Inspection handle for development only (?debug=1).
  window.__sim = { THREE, scene, camera, controls, buffer,
                   get rover() { return rover; }, get world() { return world; },
                   get grids() { return grids; }, get mission() { return mission; } };
}

(async function init() {
  frame();
  try {
    config = await getJSON('config');
    fillControls();
  } catch { /* loadMission reports the failure */ }
  await loadMission();
})();
