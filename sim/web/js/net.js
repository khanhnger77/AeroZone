// Networking for the sim page: relative-URL fetch helpers, a reconnecting WebSocket and the
// telemetry interpolation buffer.
//
// Every URL is relative to the page (principle 6), so the same page works at /sim/ (direct) and
// /.sim/ (behind the planner's proxy). Telemetry is stamped with sim_time; the buffer replays it
// about 220 ms behind real time and interpolates between samples, which hides WebSocket jitter
// (principle 7). The browser never simulates; it only renders what the server says.

export async function getJSON(url, options = {}) {
  const resp = await fetch(url, { cache: 'no-store', ...options });
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON error body */ }
  if (!resp.ok) {
    const detail = body && (body.detail || body.error);
    throw new Error(`${url}: HTTP ${resp.status}${detail ? ` - ${detail}` : ''}`);
  }
  return body;
}

export function postJSON(url, data) {
  return getJSON(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data) });
}

export async function getBinary(url) {
  const resp = await fetch(url, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`${url}: HTTP ${resp.status}`);
  return resp.arrayBuffer();
}

/** WebSocket that dispatches JSON messages by `type` and reconnects with back-off (<= 5 s). */
export class SimSocket {
  constructor(relativeUrl) {
    const u = new URL(relativeUrl, location.href);
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = u.href;
    this.handlers = new Map();
    this.delay = 500;
    this.ws = null;
    this.closedByUser = false;
    this._connect();
  }

  on(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(fn);
    return this;
  }

  _emit(type, payload) {
    for (const fn of this.handlers.get(type) || []) {
      try { fn(payload); } catch (err) { console.error(`handler for ${type} failed`, err); }
    }
  }

  get open() { return this.ws && this.ws.readyState === WebSocket.OPEN; }

  send(obj) {
    if (!this.open) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  _connect() {
    const ws = new WebSocket(this.url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => { this.delay = 500; this._emit('open'); };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') { this._emit('binary', ev.data); return; }
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg && msg.type) this._emit(msg.type, msg);
    };
    ws.onclose = () => {
      this._emit('close');
      if (this.closedByUser) return;
      setTimeout(() => this._connect(), this.delay);
      this.delay = Math.min(this.delay * 2, 5000);
    };
    ws.onerror = () => { /* onclose follows and schedules the reconnect */ };
  }

  close() { this.closedByUser = true; if (this.ws) this.ws.close(); }
}

function slerp(a, b, t) {
  // a, b: [x, y, z, w]
  let [ax, ay, az, aw] = a;
  let [bx, by, bz, bw] = b;
  let cos = ax * bx + ay * by + az * bz + aw * bw;
  if (cos < 0) { cos = -cos; bx = -bx; by = -by; bz = -bz; bw = -bw; }
  let k0, k1;
  if (cos > 0.9995) { k0 = 1 - t; k1 = t; }
  else {
    const th = Math.acos(cos), s = Math.sin(th);
    k0 = Math.sin((1 - t) * th) / s; k1 = Math.sin(t * th) / s;
  }
  const q = [ax * k0 + bx * k1, ay * k0 + by * k1, az * k0 + bz * k1, aw * k0 + bw * k1];
  const n = Math.hypot(...q) || 1;
  return q.map((c) => c / n);
}

/**
 * Replays telemetry `delayMs` behind the newest sample, in sim time.
 * The playback clock advances at the current warp, so time warp changes need no special handling.
 */
export class InterpBuffer {
  constructor(delayMs = 220, max = 120) {
    this.delayMs = delayMs;
    this.max = max;
    this.samples = [];           // { st, recv, warp, pose, wheels }
  }

  clear() { this.samples.length = 0; }

  push(t) {
    const s = { st: t.sim_time, recv: performance.now(), warp: t.warp || 1, pose: t.pose,
                wheels: t.wheels };
    const last = this.samples[this.samples.length - 1];
    if (last && s.st < last.st) this.clear();          // sim restarted
    if (last && s.st === last.st) { this.samples[this.samples.length - 1] = s; return; }
    this.samples.push(s);
    if (this.samples.length > this.max) this.samples.shift();
  }

  /** Milliseconds since the newest sample arrived (Infinity when empty). */
  age(now = performance.now()) {
    const last = this.samples[this.samples.length - 1];
    return last ? now - last.recv : Infinity;
  }

  /** Interpolated { x, y, z, q, wheels } at the playback time, or null when empty. */
  sample(now = performance.now()) {
    const n = this.samples.length;
    if (!n) return null;
    const last = this.samples[n - 1];
    const rate = last.warp / 1000;                      // sim seconds per wall millisecond
    // Do not extrapolate more than one delay past the newest sample.
    const ahead = Math.min(now - last.recv, this.delayMs);
    const target = last.st + (ahead - this.delayMs) * rate;
    if (n === 1 || target >= last.st) return { ...last.pose, wheels: last.wheels };
    if (target <= this.samples[0].st) return { ...this.samples[0].pose, wheels: this.samples[0].wheels };
    let i = n - 2;
    while (i > 0 && this.samples[i].st > target) i--;
    const a = this.samples[i], b = this.samples[i + 1];
    const t = (target - a.st) / Math.max(b.st - a.st, 1e-9);
    const pa = a.pose, pb = b.pose;
    return {
      x: pa.x + (pb.x - pa.x) * t, y: pa.y + (pb.y - pa.y) * t, z: pa.z + (pb.z - pa.z) * t,
      q: slerp(pa.q, pb.q, t), wheels: t < 0.5 ? a.wheels : b.wheels,
    };
  }
}
