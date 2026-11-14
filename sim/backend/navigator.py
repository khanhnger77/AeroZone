"""Pure-pursuit route follower: the sim's stand-in "brain" (C8).

Why this module exists: the simulator only simulates (principle 2). Driving decisions live behind a
narrow contract, pose in and (v, omega) out, so a real autonomy stack, or M5's obstacle avoidance
wrapping this navigator, can replace it without touching physics or rendering.

Algorithm:
  * Progress `s` is the arc length of the closest point on the path, searched only within a window
    of `WINDOW_SEGMENTS` segments ahead of the last match. This makes progress monotone and stops
    the rover "jumping" to a later part of a route that loops back near itself.
  * Cross-track error `xte` is signed, positive when the rover is left of the path.
  * The carrot is `ld = lookahead_m + lookahead_per_speed_s * |v|` ahead along the path. The classic
    pure-pursuit curvature `kappa = 2 sin(alpha) / ld` gives `omega = v * kappa`, clipped to the yaw
    rate limit. If the carrot is more than 60 deg off the nose, the rover stops and turns in place,
    which the four-corner steering allows.
  * Speed is `cruise * clip(remaining / slow_radius_m, 0.15, 1)` so the rover eases into the goal.
"""
from __future__ import annotations

import math

import numpy as np

WINDOW_SEGMENTS = 60
TURN_IN_PLACE_RAD = math.radians(60.0)
MIN_SPEED_FRACTION = 0.15


def _yaw_from_quat(q) -> float:
    x, y, z, w = (float(c) for c in q)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


class PurePursuitNavigator:
    """Follows a polyline in the local ENU frame. See the module docstring for the method."""

    def __init__(self, rover_cfg: dict) -> None:
        nav = rover_cfg["navigator"]
        lim = rover_cfg["limits"]
        self.lookahead_m = float(nav["lookahead_m"])
        self.lookahead_per_speed_s = float(nav["lookahead_per_speed_s"])
        self.goal_tolerance_m = float(nav["goal_tolerance_m"])
        self.slow_radius_m = float(nav["slow_radius_m"])
        self.max_yaw_rate = float(lim["max_yaw_rate_rad_s"])
        self.cruise = float(lim["default_speed_m_s"])

        self.path = np.zeros((0, 2))
        self.cum = np.zeros(0)
        self.s = 0.0
        self.xte = 0.0
        self.v = 0.0
        self.w = 0.0
        self.state = "idle"
        self._idx = 0
        self._pos = np.zeros(2)
        self._yaw = 0.0
        self._speed = 0.0

    # ------------------------------------------------------------------ path
    @property
    def length(self) -> float:
        return float(self.cum[-1]) if self.cum.size else 0.0

    def load_path(self, xy) -> None:
        """Load a route (sequence of [x, y]); resets progress and returns to `idle`."""
        pts = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        if pts.shape[0] >= 2:  # drop zero-length segments (duplicate vertices)
            keep = np.concatenate([[True], np.hypot(*np.diff(pts, axis=0).T) > 1e-6])
            pts = pts[keep]
        if pts.shape[0] < 2:
            raise ValueError("path needs at least two distinct points")
        self.path = pts
        seg = np.hypot(*np.diff(pts, axis=0).T)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.s = 0.0
        self.xte = 0.0
        self.v = self.w = 0.0
        self._idx = 0
        self.state = "idle"

    def _point_at(self, s: float) -> np.ndarray:
        """Point at arc length s along the path, clamped to its ends."""
        s = min(max(float(s), 0.0), self.length)
        k = int(np.searchsorted(self.cum, s, side="right")) - 1
        k = min(max(k, 0), len(self.path) - 2)
        seg = self.cum[k + 1] - self.cum[k]
        t = (s - self.cum[k]) / seg if seg > 0 else 0.0
        return self.path[k] + t * (self.path[k + 1] - self.path[k])

    # ------------------------------------------------------------------ control
    def set_cruise(self, v: float) -> None:
        self.cruise = max(0.0, float(v))

    def start(self) -> None:
        if self.path.shape[0] >= 2 and self.state in ("idle", "paused"):
            self.state = "driving"

    def pause(self) -> None:
        if self.state == "driving":
            self.state = "paused"
        self.v = self.w = 0.0

    def observe(self, pose) -> None:
        """Update progress, cross-track error and arrival from the rover pose."""
        self._pos = np.array([pose.x, pose.y], dtype=np.float64)
        self._yaw = _yaw_from_quat(pose.q)
        self._speed = math.hypot(pose.vx, pose.vy)
        if self.path.shape[0] < 2:
            return

        lo = self._idx
        hi = min(lo + WINDOW_SEGMENTS, len(self.path) - 1)
        a = self.path[lo:hi]
        b = self.path[lo + 1:hi + 1]
        d = b - a
        seg_len2 = np.einsum("ij,ij->i", d, d)
        t = np.clip(np.einsum("ij,ij->i", self._pos - a, d) / seg_len2, 0.0, 1.0)
        proj = a + t[:, None] * d
        dist2 = np.einsum("ij,ij->i", self._pos - proj, self._pos - proj)
        k = int(np.argmin(dist2))
        s_new = float(self.cum[lo + k] + t[k] * math.sqrt(seg_len2[k]))
        if s_new >= self.s:
            self.s = s_new
            self._idx = lo + k
        rel = self._pos - proj[k]
        cross = d[k, 0] * rel[1] - d[k, 1] * rel[0]
        self.xte = math.copysign(math.sqrt(dist2[k]), cross) if cross != 0.0 else 0.0

        if self.state == "driving":
            goal_d = float(np.hypot(*(self.path[-1] - self._pos)))
            if goal_d <= self.goal_tolerance_m or self.length - self.s < 0.5 * self.goal_tolerance_m:
                self.state = "arrived"
                self.v = self.w = 0.0

    def command(self) -> tuple[float, float]:
        """(v, omega) for the current state; zero unless driving."""
        if self.state != "driving":
            self.v = self.w = 0.0
            return 0.0, 0.0
        remaining = max(0.0, self.length - self.s)
        ld = self.lookahead_m + self.lookahead_per_speed_s * self._speed
        target = self._point_at(self.s + ld)
        to_target = target - self._pos
        alpha = _wrap_pi(math.atan2(to_target[1], to_target[0]) - self._yaw)
        if abs(alpha) > TURN_IN_PLACE_RAD:
            v = 0.0
            w = math.copysign(self.max_yaw_rate, alpha)
        else:
            v = self.cruise * min(max(remaining / self.slow_radius_m, MIN_SPEED_FRACTION), 1.0)
            w = v * 2.0 * math.sin(alpha) / ld
            w = min(max(w, -self.max_yaw_rate), self.max_yaw_rate)
        self.v, self.w = float(v), float(w)
        return self.v, self.w

    def status(self) -> dict:
        """The `nav` block of C6 telemetry."""
        length = self.length
        return {
            "state": self.state,
            "progress": (self.s / length) if length > 0 else 0.0,
            "distance_done_m": self.s,
            "distance_remaining_m": max(0.0, length - self.s),
            "length_m": length,
            "cross_track_m": self.xte,
            "cmd_v": self.v,
            "cmd_w": self.w,
            "cruise_m_s": self.cruise,
        }
