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
  world.add(ribbon(m.route.xy, sampler, { width: 1.6, color: m.route.color, opacity: 0.9, lift: 0.15 }));
  const [sx, sy] = m.route.xy[0];
  const [ex, ey] = m.route.xy[m.route.xy.length - 1];
  world.add(marker(sx, sy, sampler, '#ffffff'));
  world.add(marker(ex, ey, sampler, m.route.color, { pole: true }));
  scene.add(world);
}

async function addObstacles(m) {
  if (!m.obstacles || !m.obstacles.enabled) return;
  try {
    const mod = await import('../obstacle_avoid/obstacles_view.js');
    world.add(mod.rocksGroup(m.obstacles.rocks || [], sampler));
    world.add(mod.cratersGroup(m.obstacles.craters || [], sampler));
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

function setCamera(mode, force = false) {
  if (!['chase', 'orbit', 'overview'].includes(mode)) return;
  if (mode === cameraMode && !force) return;
  cameraMode = mode;
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
  syncSelect('sel-model', t.model);
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
  $('sel-model').innerHTML = c.models.map((m) =>
    `<option value="${m}">${{ wheeled: 'Wheeled (PyBullet)', kinematic: 'Kinematic', astronaut: 'Astronaut (on foot)' }[m] || m}</option>`).join('');
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
$('sel-model').addEventListener('change', (e) => command({ type: 'set_model', value: e.target.value }));
document.querySelectorAll('[data-cam]').forEach((b) => b.addEventListener('click', () => setCamera(b.dataset.cam)));

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
  } else if (d.type === 'camera') {
    setCamera(d.mode);
  }
});

// The figure that carries the pose: the astronaut when the "astronaut" motion model is active.
function onFoot() { return !!(astro && latest && latest.model === 'astronaut'); }
function activeBody() { return onFoot() ? astro : rover; }

// ------------------------------------------------------------------------------- frame loop
const clock = new THREE.Clock();
function frame() {
  requestAnimationFrame(frame);
  const dt = Math.min(clock.getDelta(), 0.1);
  if (rover && grids) {
    const s = buffer.sample();
    if (s) {
      const foot = onFoot();
      rover.root.visible = !foot;
      if (astro) astro.root.visible = foot;
      const body = activeBody();
      body.setPose(s);
      if (foot) astro.setGait(s.wheels && s.wheels[0] ? s.wheels[0].spin : 0, latest ? latest.speed_m_s : 0);
      else rover.setWheels(s.wheels);
      roverPos.copy(body.root.position);
    }
    updateCamera(dt);
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
