// Perseverance in the 3D view. The server owns the pose; this only draws it.
//
// The NASA glb is Y-up, faces +Z, in metres, origin at ground contact. We centre it in x/z, put its
// lowest point at y = 0 and rotate it +90 deg about Y so it faces +X (ENU east = yaw 0, C1).
// If loading fails, a primitive stand-in (box chassis + six wheels) is shown instead.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';

const REAL_LENGTH_M = 2.9;

export class RoverView {
  constructor(roverCfg) {
    this.cfg = roverCfg;
    this.root = new THREE.Group();     // carries the pose
    this.root.name = 'rover';
    this.wheels = [];                  // stand-in wheels: { steerPivot, spinner }
    this.isModel = false;
  }

  /** Load the NASA model; resolves true on success, false when the stand-in is used. */
  async load(url) {
    try {
      const draco = new DRACOLoader();
      draco.setDecoderPath('vendor/three/addons/libs/draco/gltf/');
      const loader = new GLTFLoader();
      loader.setDRACOLoader(draco);
      const gltf = await loader.loadAsync(url);
      draco.dispose();
      this._useModel(gltf.scene);
      return true;
    } catch (err) {
      console.warn('rover model failed to load, using stand-in', err);
      this._useStandIn();
      return false;
    }
  }

  _useModel(scene) {
    const box = new THREE.Box3().setFromObject(scene);
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    scene.position.set(-centre.x, -box.min.y, -centre.z);
    const holder = new THREE.Group();
    holder.add(scene);
    const lengthZ = size.z;                                   // model faces +Z
    if (Math.abs(lengthZ - REAL_LENGTH_M) / REAL_LENGTH_M > 0.15) {
      holder.scale.setScalar(REAL_LENGTH_M / lengthZ);
    }
    holder.rotation.y = Math.PI / 2;                          // +Z forward -> +X forward
    scene.traverse((o) => {
      if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; }
    });
    this.root.add(holder);
    this.isModel = true;
  }

  _useStandIn() {
    const d = this.cfg || {};
    const [L, W, H] = d.chassis_size_m || [2.9, 2.7, 0.9];
    const r = d.wheel_radius_m || 0.2625;
    const clearance = 0.66;
    const body = new THREE.Mesh(new THREE.BoxGeometry(L, H, W),
                                new THREE.MeshStandardMaterial({ color: 0xe8e4dc, roughness: 0.6 }));
    body.position.y = clearance + H / 2;
    body.castShadow = true;
    this.root.add(body);
    const wheelGeo = new THREE.CylinderGeometry(r, r, 0.4, 20);
    wheelGeo.rotateX(Math.PI / 2);                           // axle along three Z (body y)
    const wheelMat = new THREE.MeshStandardMaterial({ color: 0x555555, roughness: 0.8 });
    const strutMat = new THREE.MeshStandardMaterial({ color: 0x999999, roughness: 0.5 });
    for (const [x, y] of d.wheel_positions_m || []) {
      const steerPivot = new THREE.Group();
      steerPivot.position.set(x, r, -y);                      // body (x fwd, y left) -> three (x, -z)
      const spinner = new THREE.Mesh(wheelGeo, wheelMat);
      spinner.castShadow = true;
      steerPivot.add(spinner);
      const strut = new THREE.Mesh(new THREE.BoxGeometry(0.08, clearance + H / 2 - r, 0.08), strutMat);
      strut.position.y = (clearance + H / 2 - r) / 2;
      steerPivot.add(strut);
      this.root.add(steerPivot);
      this.wheels.push({ steerPivot, spinner });
    }
    this.isModel = false;
  }

  /** Pose in ENU: { x, y, z, q: [qx, qy, qz, qw] } -> three.js via (x, z, -y), (qx, qz, -qy, qw). */
  setPose(p) {
    this.root.position.set(p.x, p.z, -p.y);
    this.root.quaternion.set(p.q[0], p.q[2], -p.q[1], p.q[3]);
  }

  /** Smoothly move towards a pose (used for camera-independent easing if needed). */
  lerpPose(p, alpha) {
    const target = new THREE.Vector3(p.x, p.z, -p.y);
    this.root.position.lerp(target, alpha);
    this.root.quaternion.slerp(new THREE.Quaternion(p.q[0], p.q[2], -p.q[1], p.q[3]), alpha);
  }

  /** Wheel steer/spin (rad) from telemetry. Only the stand-in has separate wheels. */
  setWheels(wheels) {
    if (!wheels || !this.wheels.length) return;
    wheels.forEach((w, k) => {
      const wv = this.wheels[k];
      if (!wv) return;
      wv.steerPivot.rotation.y = w.steer;                   // about body z (three +Y)
      wv.spinner.rotation.z = -w.spin;                      // about body y (three -Z)
    });
  }
}
