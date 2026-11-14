// A suited astronaut on foot (the "astronaut" motion model). Built from primitives so it needs no
// asset download or licence: white EVA suit, gold sun visor, life-support backpack (PLSS), boots.
// The server owns the pose (upright, yaw only) and the gait phase (telemetry wheels[k].spin);
// this view only draws it. Body frame as the rover: x forward, y left, z up -> three (x, z, -y).

import * as THREE from 'three';

const SUIT = 0xf1efe8;
const SOFT = 0xd9d6cc;      // joints / soft goods
const DARK = 0x3b3f46;      // boots, gloves
const ACCENT = 0xff6a3d;    // mission stripe

function mat(color, opts = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.0, ...opts });
}

function capsule(radius, length, material) {
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(radius, length, 6, 14), material);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

/** A limb hanging down from a pivot: upper segment, joint, lower segment, end piece. */
function limb({ upper, lower, radius, end }) {
  const pivot = new THREE.Group();
  const up = capsule(radius, upper, mat(SUIT));
  up.position.y = -upper / 2 - radius * 0.3;
  pivot.add(up);
  const knee = new THREE.Group();             // second joint (knee / elbow)
  knee.position.y = -upper - radius * 0.6;
  pivot.add(knee);
  const jointBall = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.05, 14, 10), mat(SOFT));
  jointBall.castShadow = true;
  knee.add(jointBall);
  const lo = capsule(radius * 0.92, lower, mat(SUIT));
  lo.position.y = -lower / 2 - radius * 0.3;
  knee.add(lo);
  if (end) {
    end.position.y = -lower - radius * 0.8;
    knee.add(end);
  }
  return { pivot, knee };
}

export class AstronautView {
  constructor(cfg = {}) {
    const H = cfg.height_m || 1.85;
    const s = H / 1.85;                        // everything below is designed for 1.85 m
    this.root = new THREE.Group();             // carries the pose
    this.root.name = 'astronaut';
    this.body = new THREE.Group();             // bobs with the gait
    this.body.scale.setScalar(s);
    this.root.add(this.body);

    // Torso (hard upper torso) and hips.
    const torso = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.52, 0.5, 2, 2, 2), mat(SUIT));
    torso.position.set(0, 1.2, 0);
    torso.castShadow = true;
    this.body.add(torso);
    const chestPlate = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.18, 0.3), mat(SOFT));
    chestPlate.position.set(0.19, 1.22, 0);           // display & control module on the chest
    this.body.add(chestPlate);
    const stripe = new THREE.Mesh(new THREE.BoxGeometry(0.345, 0.05, 0.505), mat(ACCENT));
    stripe.position.set(0, 1.0, 0);
    this.body.add(stripe);
    const hips = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.2, 18), mat(SOFT));
    hips.position.set(0, 0.92, 0);
    hips.castShadow = true;
    this.body.add(hips);

    // Life-support backpack (PLSS) behind the torso.
    const plss = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.62, 0.46), mat(SUIT, { roughness: 0.6 }));
    plss.position.set(-0.3, 1.24, 0);
    plss.castShadow = true;
    this.body.add(plss);
    const plssTop = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.06, 0.4), mat(SOFT));
    plssTop.position.set(-0.3, 1.58, 0);
    this.body.add(plssTop);

    // Helmet: clear bubble with a gold sun visor over the face.
    const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.17, 24, 18), mat(SUIT, { roughness: 0.35 }));
    helmet.position.set(0.02, 1.62, 0);
    helmet.castShadow = true;
    this.body.add(helmet);
    const visor = new THREE.Mesh(
      new THREE.SphereGeometry(0.155, 24, 16, -Math.PI / 2.4, Math.PI / 1.2, Math.PI / 4, Math.PI / 2.1),
      mat(0xd4a017, { metalness: 0.9, roughness: 0.18, emissive: 0x3a2600 }));
    visor.position.set(0.045, 1.62, 0);
    visor.rotation.y = Math.PI / 2;                    // face +X (forward)
    this.body.add(visor);
    const neckRing = new THREE.Mesh(new THREE.TorusGeometry(0.15, 0.025, 8, 24), mat(SOFT));
    neckRing.position.set(0, 1.47, 0);
    neckRing.rotation.x = Math.PI / 2;
    this.body.add(neckRing);

    // Legs (hip pivots) and arms (shoulder pivots). Left = -Z in three.
    const boot = () => {
      const b = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.1, 0.14), mat(DARK));
      b.position.x = 0.05;
      b.castShadow = true;
      return b;
    };
    const glove = () => {
      const g = new THREE.Mesh(new THREE.SphereGeometry(0.055, 12, 10), mat(DARK));
      g.castShadow = true;
      return g;
    };
    this.legs = [-1, 1].map((side) => {
      const l = limb({ upper: 0.32, lower: 0.3, radius: 0.075, end: boot() });
      l.pivot.position.set(0, 0.86, side * 0.1);
      this.body.add(l.pivot);
      return l;
    });
    this.arms = [-1, 1].map((side) => {
      const a = limb({ upper: 0.24, lower: 0.22, radius: 0.06, end: glove() });
      a.pivot.position.set(0.0, 1.4, side * 0.3);
      a.pivot.rotation.x = side * 0.12;                // arms slightly away from the body
      this.body.add(a.pivot);
      return a;
    });
    this.legs.forEach((l, i) => { l.side = i === 0 ? 1 : -1; });
    this.arms.forEach((a, i) => { a.side = i === 0 ? -1 : 1; });
    this.setGait(0, 0);
  }

  /** Pose in ENU (yaw-only quaternion from the server) -> three via (x, z, -y), (qx, qz, -qy, qw). */
  setPose(p) {
    this.root.position.set(p.x, p.z, -p.y);
    this.root.quaternion.set(p.q[0], p.q[2], -p.q[1], p.q[3]);
  }

  /** Walk cycle: phase in rad (one cycle = two steps), speed in m/s scales the stride. */
  setGait(phase, speed) {
    const k = Math.min(1, Math.abs(speed || 0) / 1.0);        // stride grows with speed
    const swing = 0.15 + 0.4 * k;
    this.legs.forEach((l) => {
      const a = Math.sin(phase) * l.side;
      l.pivot.rotation.z = swing * a;                          // hip flexion (about lateral axis)
      l.knee.rotation.z = -Math.max(0, -Math.cos(phase) * l.side) * (0.25 + 0.5 * k);   // knee bends on the swing
    });
    this.arms.forEach((a) => {
      const b = Math.sin(phase) * a.side;
      a.pivot.rotation.z = 0.6 * swing * b;                    // arms counter-swing
      a.knee.rotation.z = 0.25 + 0.15 * k;                     // elbows slightly bent
    });
    this.body.position.y = 0.025 * k * Math.abs(Math.sin(phase));   // bob twice per cycle
    this.body.rotation.x = 0.03 * k * Math.sin(phase);              // slight sway
  }
}
