// Terrain meshes, route ribbons and markers built from the server's height grids (C5).
//
// The corridor grid served here is the very grid PyBullet collides with (principle 5), so the rover
// sits on exactly what is drawn. Mapping (C1): ENU (x, y, z) -> three.js (x, z, -y).
// Grid layout (C5): h[j * nx + i], row j south -> north, column i west -> east, metres from z0.

import * as THREE from 'three';
import { getBinary } from './net.js';

const BASE_COLOR = 0xb8754f;     // untextured regolith tone
const TEXTURE_TINT = 0xffc6a0;   // warms the greyscale orthoimages

export class HeightGrid {
  constructor(desc, heights) {
    Object.assign(this, { nx: desc.nx, ny: desc.ny, x0: desc.x0, y0: desc.y0, dx: desc.dx,
                          dy: desc.dy, desc });
    this.h = heights;
  }

  get xMax() { return this.x0 + (this.nx - 1) * this.dx; }
  get yMax() { return this.y0 + (this.ny - 1) * this.dy; }

  contains(x, y) {
    return x >= this.x0 && x <= this.xMax && y >= this.y0 && y <= this.yMax;
  }

  /** Bilinear height (m from z0), clamped to the grid edge. */
  heightAt(x, y) {
    const { nx, ny, h } = this;
    const fx = Math.min(Math.max((x - this.x0) / this.dx, 0), nx - 1);
    const fy = Math.min(Math.max((y - this.y0) / this.dy, 0), ny - 1);
    const i = Math.min(Math.floor(fx), nx - 2), j = Math.min(Math.floor(fy), ny - 2);
    const tx = fx - i, ty = fy - j;
    const k = j * nx + i;
    return (1 - tx) * (1 - ty) * h[k] + tx * (1 - ty) * h[k + 1]
      + (1 - tx) * ty * h[k + nx] + tx * ty * h[k + nx + 1];
  }
}

export async function loadGrid(level, desc, missionId) {
  const buf = await getBinary(`api/terrain/${level}.bin?m=${encodeURIComponent(missionId)}`);
  const expected = desc.nx * desc.ny * 4;
  if (buf.byteLength !== expected) {
    throw new Error(`${level} terrain: got ${buf.byteLength} bytes, expected ${expected}`);
  }
  return new HeightGrid(desc, new Float32Array(buf));   // little-endian on every browser platform
}

/**
 * Indexed geometry for a grid. `hole` = {xMin, xMax, yMin, yMax} (local m): quads whose centre is
 * inside it are skipped, so the coarse context mesh does not cover the detailed corridor.
 */
export function gridGeometry(g, hole = null) {
  const { nx, ny } = g;
  const pos = new Float32Array(nx * ny * 3);
  const uv = new Float32Array(nx * ny * 2);
  for (let j = 0; j < ny; j++) {
    const y = g.y0 + j * g.dy;
    for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      pos[3 * k] = g.x0 + i * g.dx;
      pos[3 * k + 1] = g.h[k];
      pos[3 * k + 2] = -y;
      uv[2 * k] = (i + 0.5) / nx;
      uv[2 * k + 1] = (j + 0.5) / ny;
    }
  }
  const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let n = 0;
  for (let j = 0; j < ny - 1; j++) {
    const yc = g.y0 + (j + 0.5) * g.dy;
    const rowInHole = hole && yc > hole.yMin && yc < hole.yMax;
    for (let i = 0; i < nx - 1; i++) {
      if (rowInHole) {
        const xc = g.x0 + (i + 0.5) * g.dx;
        if (xc > hole.xMin && xc < hole.xMax) continue;
      }
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      // Counter-clockwise seen from +Y (up): (a, b, c) and (b, d, c).
      idx[n++] = a; idx[n++] = b; idx[n++] = c;
      idx[n++] = b; idx[n++] = d; idx[n++] = c;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geo.setIndex(new THREE.BufferAttribute(idx.subarray(0, n), 1));
  geo.computeVertexNormals();
  geo.computeBoundingSphere();
  return geo;
}

/** Terrain mesh; the orthoimage texture is applied when (and if) it loads. */
export function terrainMesh(grid, { textureUrl = null, renderer = null, hole = null, onTexture } = {}) {
  const material = new THREE.MeshStandardMaterial({ color: BASE_COLOR, roughness: 0.97,
                                                    metalness: 0.0 });
  const mesh = new THREE.Mesh(gridGeometry(grid, hole), material);
  mesh.receiveShadow = true;
  if (textureUrl) {
    new THREE.TextureLoader().load(textureUrl, (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      if (renderer) tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
      material.map = tex;
      material.color.set(TEXTURE_TINT);
      material.needsUpdate = true;
      if (onTexture) onTexture(true);
    }, undefined, () => { if (onTexture) onTexture(false); });
  }
  return mesh;
}

/** Densify a polyline so a draped strip follows the terrain between sparse vertices. */
function densify(xy, step) {
  const out = [];
  for (let k = 0; k < xy.length - 1; k++) {
    const [x0, y0] = xy[k], [x1, y1] = xy[k + 1];
    const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let s = 0; s < n; s++) out.push([x0 + ((x1 - x0) * s) / n, y0 + ((y1 - y0) * s) / n]);
  }
  out.push(xy[xy.length - 1]);
  return out;
}

/** Flat strip of `width` metres draped on the terrain along `xy` (local ENU). */
export function ribbon(xy, sampler, { width = 1.6, color = '#34d399', opacity = 0.9, lift = 0.15 } = {}) {
  const pts = densify(xy, 2.0);
  const n = pts.length;
  const pos = new Float32Array(n * 2 * 3);
  for (let k = 0; k < n; k++) {
    const a = pts[Math.max(0, k - 1)], b = pts[Math.min(n - 1, k + 1)];
    let tx = b[0] - a[0], ty = b[1] - a[1];
    const len = Math.hypot(tx, ty) || 1;
    tx /= len; ty /= len;
    const lx = -ty * width / 2, ly = tx * width / 2;      // left normal
    const [x, y] = pts[k];
    for (const [s, off] of [[0, 1], [1, -1]]) {
      const px = x + off * lx, py = y + off * ly;
      const q = (2 * k + s) * 3;
      pos[q] = px; pos[q + 1] = sampler(px, py) + lift; pos[q + 2] = -py;
    }
  }
  const idx = [];
  for (let k = 0; k < n - 1; k++) {
    const l0 = 2 * k, r0 = l0 + 1, l1 = l0 + 2, r1 = l0 + 3;
    idx.push(l0, r0, l1, r0, r1, l1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setIndex(idx);
  const mat = new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity,
                                            depthWrite: false, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 2;
  return mesh;
}

/** Ground ring at (x, y), optionally with a pole and flag (the destination). */
export function marker(x, y, sampler, color = '#ffffff', { pole = false, radius = 3 } = {}) {
  const group = new THREE.Group();
  const z = sampler(x, y);
  group.position.set(x, z, -y);
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(radius * 0.75, radius, 48),
    new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: 0.9,
                                  depthWrite: false }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.2;
  ring.renderOrder = 3;
  group.add(ring);
  if (pole) {
    const h = 8;
    const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, h, 8),
                                 new THREE.MeshStandardMaterial({ color: 0xdddddd, roughness: 0.6 }));
    stick.position.y = h / 2;
    stick.castShadow = true;
    const flag = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.4),
                                new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide,
                                                                 roughness: 0.8 }));
    flag.position.set(1.2, h - 0.8, 0);
    group.add(stick, flag);
  }
  return group;
}

/** Free GPU resources of a subtree. */
export function disposeTree(obj) {
  obj.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) {
      if (m.map) m.map.dispose();
      m.dispose();
    }
  });
}
