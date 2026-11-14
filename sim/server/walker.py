"""Suited astronaut on foot: the third motion model of ``RoverModelRouter`` ("astronaut").

Why a kinematic walker (no PyBullet body): a person on an EVA does not tip like a rover, so the
interesting physics is not contact dynamics but *how fast a suited human can walk on a given slope*.
The model therefore

* integrates (v, omega) with short first-order lags (people accelerate and turn faster than a rover);
* stays upright (pitch = roll = 0) and follows the terrain height under the feet;
* caps speed at a suited walking pace and scales it with the slope along the walking direction, using
  Tobler's hiking function normalised to flat ground and softened for the suit; slopes at or above
  ``no_go_slope_deg`` stop uphill progress (the same no-go threshold as the M2 route planner);
* exposes a gait phase (one stride cycle per ``2 * stride_m`` walked) for the walking animation.

Values come from ``sim/configs/astronaut.yaml``. Telemetry keeps the C6 shape: ``wheels()`` returns six
entries whose ``spin`` is the gait phase (rad) and ``steer`` is 0, so clients that ignore the astronaut
still parse it.
"""
from __future__ import annotations

import math

from sim.server.physics import Pose, quat_from_euler


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def tobler_factor(grade: float, softening: float = 1.0) -> float:
    """Walking-speed factor vs flat ground for a slope ``grade`` = dz/ds along the walking direction.

    Tobler: v = 6·exp(−3.5·|grade + 0.05|) km/h, fastest on a slight downhill. Normalised so flat = 1;
    ``softening`` < 1 flattens the curve (a suit limits the downhill gain and the uphill effort alike).
    """
    f = math.exp(-3.5 * abs(grade + 0.05)) / math.exp(-3.5 * 0.05)
    return f ** softening


class AstronautWalker:
    """Upright, terrain-following walker driven by (v, omega) from the navigator / obstacle avoidance."""

    name = "astronaut"

    def __init__(self, acfg: dict) -> None:
        w = acfg.get("walker", acfg)
        self.label = str(acfg.get("name", "EVA astronaut"))
        self.height = float(acfg.get("height_m", 1.85))
        self.max_speed = float(w["max_speed_m_s"])
        self.max_yaw_rate = float(w["max_yaw_rate_rad_s"])
        self.lag_v = float(w["speed_lag_s"])
        self.lag_w = float(w["turn_lag_s"])
        self.no_go = math.tan(math.radians(float(w["no_go_slope_deg"])))
        self.min_factor = float(w["min_speed_factor"])
        self.softening = float(w["slope_softening"])
        self.stride = float(w["stride_m"])
        self.probe = float(w["slope_probe_m"])
        self.grid = None
        self.x = self.y = self.yaw = 0.0
        self.v = self.w = 0.0
        self.gait = 0.0          # rad, one full cycle = two steps
        self.grade = 0.0         # last slope along the heading (dz/ds)

    def set_grid(self, grid) -> None:
        self.grid = grid

    def _h(self, x: float, y: float) -> float:
        return float(self.grid.height_at(x, y)) if self.grid is not None else 0.0

    def spawn(self, x: float, y: float, yaw: float, drop: float = 0.0, v: float = 0.0, w: float = 0.0) -> None:
        self.x, self.y, self.yaw = float(x), float(y), float(yaw)
        self.v, self.w = float(v), float(w)
        self.gait = 0.0

    def speed_factor(self, heading: float, direction: float = 1.0) -> float:
        """Speed factor for walking along ``heading`` (direction −1 = backwards)."""
        c, s = math.cos(heading), math.sin(heading)
        d = self.probe
        grade = direction * (self._h(self.x + d * c, self.y + d * s) - self._h(self.x - d * c, self.y - d * s)) / (2 * d)
        self.grade = grade
        if grade >= self.no_go:
            return 0.0                                   # too steep to climb in a suit
        return max(self.min_factor, min(1.0, tobler_factor(grade, self.softening)))

    def step(self, dt: float, v_cmd: float, w_cmd: float) -> None:
        w_cmd = max(-self.max_yaw_rate, min(self.max_yaw_rate, w_cmd))
        direction = 1.0 if v_cmd >= 0 else -1.0
        v_cap = self.max_speed * self.speed_factor(self.yaw, direction)
        v_cmd = max(-0.5 * v_cap, min(v_cap, v_cmd))     # stepping backwards is slower
        self.v += (v_cmd - self.v) * (1.0 - math.exp(-dt / self.lag_v))
        self.w += (w_cmd - self.w) * (1.0 - math.exp(-dt / self.lag_w))
        self.yaw = _wrap_pi(self.yaw + self.w * dt)
        ds = self.v * dt
        self.x += ds * math.cos(self.yaw)
        self.y += ds * math.sin(self.yaw)
        turning = abs(self.w) * 0.35                     # small steps on the spot when turning
        self.gait = (self.gait + (abs(ds) + turning * dt) * math.pi / self.stride) % (2.0 * math.pi)

    def pose(self) -> Pose:
        q = quat_from_euler(0.0, 0.0, self.yaw)          # people stay upright on slopes
        return Pose(self.x, self.y, self._h(self.x, self.y), q,
                    self.v * math.cos(self.yaw), self.v * math.sin(self.yaw), 0.0, self.w)

    def wheels(self) -> list[dict]:
        """C6 shape kept: six entries; ``spin`` carries the gait phase for the walking animation."""
        return [{"steer": 0.0, "spin": float(self.gait)} for _ in range(6)]

    def describe(self) -> dict:
        return {"name": self.label, "height_m": self.height, "max_speed_m_s": self.max_speed,
                "no_go_slope_deg": round(math.degrees(math.atan(self.no_go)), 1)}
