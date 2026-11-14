// M5 browser module (contract C8). Imported dynamically by M4's main.js only when
// mission.obstacles.enabled. ENU (x, y, z) maps to Three.js (x, z, -y): a proper rotation,
// so the server's outward triangle winding is kept as is.
import * as THREE from 'three';

const ROCK_COLOR = 0x3d332e;
const RING_COLOR = 0xff3b30;
const RIM_COLOR = 0xff8c32;
const ARC_COLOR = 0x5ee6ff;

/** Boulders (server hull meshes) plus a red hazard ring around each. */
export function rocksGroup(rocks, sampler) {
  const group = new THREE.Group();
  group.name = 'm5-rocks';
  const rockMat = new THREE.MeshStandardMaterial({ color: ROCK_COLOR, roughness: 0.85, metalness: 0, flatShading: true });
  const ringMat = new THREE.MeshBasicMaterial({
    color: RING_COLOR, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false,
  });
  for (const r of rocks || []) {
    const v = r.v || [];
    const pos = new Float32Array(v.length);
    for (let i = 0; i < v.length; i += 3) {
      pos[i] = v[i];          // x
      pos[i + 1] = v[i + 2];  // z (up)
      pos[i + 2] = -v[i + 1]; // -y
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setIndex(r.f || []);
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, rockMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.name = `rock-${r.id}`;
    group.add(mesh);

    const ring = new THREE.Mesh(new THREE.RingGeometry(1.25 * r.radius, 1.25 * r.radius + 0.12, 64), ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(r.x, sampler(r.x, r.y) + 0.08, -r.y);
    ring.renderOrder = 2;
    group.add(ring);
  }
  return group;
}

/** Orange rim line around each crater (the crater itself is carved into the terrain grid). */
export function cratersGroup(craters, sampler) {
  const group = new THREE.Group();
  group.name = 'm5-craters';
  const mat = new THREE.LineBasicMaterial({ color: RIM_COLOR, transparent: true, opacity: 0.95 });
  const N = 72;
  for (const c of craters || []) {
    const pts = [];
    for (let k = 0; k < N; k++) {
      const a = (k / N) * Math.PI * 2;
      const x = c.x + c.radius * Math.cos(a);
      const y = c.y + c.radius * Math.sin(a);
      pts.push(new THREE.Vector3(x, sampler(x, y) + 0.4, -y));
    }
    const loop = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), mat);
    loop.name = `crater-${c.id}`;
    group.add(loop);
  }
  return group;
}

/** Cyan line along the planned detour (Telemetry.avoid.arc); hidden while tracking the route. */
export class ArcView {
  constructor(sampler) {
    this.sampler = sampler;
    this.capacity = 0;
    this.geometry = new THREE.BufferGeometry();
    this.material = new THREE.LineBasicMaterial({ color: ARC_COLOR, depthTest: false, transparent: true });
    this.line = new THREE.Line(this.geometry, this.material);
    this.line.renderOrder = 3;
    this.line.frustumCulled = false;
    this.line.visible = false;
    this._ensure(64);
  }

  _ensure(n) {
    if (n <= this.capacity) return;
    this.capacity = Math.max(n, this.capacity * 2);
    this.positions = new Float32Array(this.capacity * 3);
    this.geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
  }

  update(avoid) {
    const arc = avoid && avoid.arc;
    if (!avoid || avoid.mode === 'track' || !arc || arc.length < 2) {
      this.line.visible = false;
      return;
    }
    this._ensure(arc.length);
    const p = this.positions;
    for (let i = 0; i < arc.length; i++) {
      const [x, y] = arc[i];
      p[3 * i] = x;
      p[3 * i + 1] = this.sampler(x, y) + 0.5;
      p[3 * i + 2] = -y;
    }
    this.geometry.attributes.position.needsUpdate = true;
    this.geometry.setDrawRange(0, arc.length);
    this.line.visible = true;
  }
}
