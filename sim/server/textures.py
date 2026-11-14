"""Orthoimage textures for the terrain meshes, mosaicked from NASA Mars Trek WMTS tiles.

Why this module exists: the elevation grid alone renders as an untextured surface. Mars Trek serves
the Jezero CTX (6 m) and HiRISE (25 cm) orthomosaics as equirectangular (latitude of true scale 0,
centre longitude 0) tiles, the same projection as the C2 rasters. So a texture is a plain
lon/lat crop, with no reprojection, and it lines up with the mesh UVs.

Tile scheme (geographic, two tiles at zoom 0): `tile_deg = 180 / 2**z`, column
`x = floor((lon + 180) / tile_deg)`, row `y = floor((90 - lat) / tile_deg)`, 256 px per tile.

* The context texture uses CTX only.
* The corridor texture uses CTX as the base with HiRISE composited on top. HiRISE is first
  tone-matched to the CTX mean and standard deviation per channel, measured where both exist, so the
  footprint does not show as a hard seam.

A background warm-up and browser requests can race for the same file, so each output path has a lock
and is written atomically (`.part`, then `os.replace`). Tiles are cached on disk forever. A network
failure is remembered for a short time so a missing network does not stall every request.
"""
from __future__ import annotations

import io
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

TILE_PX = 256
FETCH_THREADS = 16
FETCH_TIMEOUT_S = 15.0
FAILURE_MEMO_S = 60.0
JPEG_QUALITY = 88
BASE_RGB = (184, 117, 79)        # #b8754f, the mesh's untextured colour
USER_AGENT = "mars-odyssey-sim/1.0"


class TextureUnavailable(RuntimeError):
    """Raised when no imagery could be obtained for a texture."""


def tile_deg(z: int) -> float:
    return 180.0 / (2 ** z)


def choose_zoom(lon_w: float, lon_e: float, lat_s: float, lat_n: float, max_zoom: int,
                max_px: int) -> int:
    """Highest zoom <= max_zoom whose crop of the extent stays within max_px on both sides."""
    span = max(lon_e - lon_w, lat_n - lat_s)
    for z in range(int(max_zoom), -1, -1):
        if span * TILE_PX / tile_deg(z) <= max_px:
            return z
    return 0


class TextureService:
    """Builds and caches `<mission>_<level>.jpg` textures for C5 `/sim/api/texture/{level}.jpg`."""

    def __init__(self, tcfg: dict, cache_dir: str | Path) -> None:
        self.provider = str(tcfg["provider"]).rstrip("/")
        self.context_layer = dict(tcfg["context_layer"])
        self.corridor_layer = dict(tcfg["corridor_layer"])
        self.max_px = int(tcfg["max_texture_px"])
        self.cache_dir = Path(cache_dir)
        self.tile_dir = self.cache_dir / "tiles"
        self.out_dir = self.cache_dir / "textures"
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._failed_at: dict[Path | str, float] = {}

    # ----------------------------------------------------------------------------------- tiles
    def tile_url(self, product: str, ext: str, z: int, y: int, x: int) -> str:
        return f"{self.provider}/{product}/1.0.0/default/default028mm/{z}/{y}/{x}.{ext}"

    def _fetch_tile(self, product: str, ext: str, z: int, y: int, x: int):
        """Return (PIL RGBA image | None, status) where status is 'ok', 'missing' or 'error'."""
        from PIL import Image

        path = self.tile_dir / product / str(z) / str(y) / f"{x}.{ext}"
        missing = path.with_suffix(".missing")
        if missing.exists():
            return None, "missing"
        if not path.exists():
            req = urllib.request.Request(self.tile_url(product, ext, z, y, x),
                                         headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:  # outside the mosaic footprint: remember, it never changes
                    missing.parent.mkdir(parents=True, exist_ok=True)
                    missing.touch()
                    return None, "missing"
                return None, "error"
            except (urllib.error.URLError, OSError):
                return None, "error"
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + f".{threading.get_ident()}.part")
            part.write_bytes(data)
            os.replace(part, path)
        try:
            img = Image.open(path)
            img.load()
        except Exception:  # corrupt cache entry: drop it so the next attempt refetches
            path.unlink(missing_ok=True)
            return None, "error"
        return img.convert("RGBA"), "ok"

    def mosaic(self, layer: dict, z: int, lon_w: float, lon_e: float, lat_s: float, lat_n: float,
               out_size: tuple[int, int]):
        """RGBA image of `layer` at zoom z cropped exactly to the edges, resampled to out_size.

        Returns (image, counts) with counts = {'ok': n, 'missing': n, 'error': n}.
        """
        from PIL import Image

        td = tile_deg(z)
        x0 = int(math.floor((lon_w + 180.0) / td))
        x1 = int(math.floor((lon_e + 180.0 - 1e-12) / td))
        y0 = int(math.floor((90.0 - lat_n) / td))
        y1 = int(math.floor((90.0 - lat_s - 1e-12) / td))
        jobs = [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        product, ext = layer["product"], layer.get("ext", "png")
        # Probe one tile first: when offline, fail in one timeout instead of one per tile.
        first = self._fetch_tile(product, ext, z, *jobs[0])
        if first[1] == "error":
            raise TextureUnavailable(f"tile provider unreachable: {self.provider}")
        with ThreadPoolExecutor(FETCH_THREADS) as pool:
            results = [first] + list(pool.map(lambda yx: self._fetch_tile(product, ext, z, *yx),
                                              jobs[1:]))

        canvas = Image.new("RGBA", ((x1 - x0 + 1) * TILE_PX, (y1 - y0 + 1) * TILE_PX), (0, 0, 0, 0))
        counts = {"ok": 0, "missing": 0, "error": 0}
        for (y, x), (img, status) in zip(jobs, results):
            counts[status] += 1
            if img is not None:
                if img.size != (TILE_PX, TILE_PX):
                    img = img.resize((TILE_PX, TILE_PX))
                canvas.paste(img, ((x - x0) * TILE_PX, (y - y0) * TILE_PX))

        ppd = TILE_PX / td
        lon_org = x0 * td - 180.0
        lat_top = 90.0 - y0 * td
        box = ((lon_w - lon_org) * ppd, (lat_top - lat_n) * ppd,
               (lon_e - lon_org) * ppd, (lat_top - lat_s) * ppd)
        return canvas.resize(out_size, Image.Resampling.LANCZOS, box=box), counts

    # ----------------------------------------------------------------------------------- render
    def _out_size(self, z: int, lon_w, lon_e, lat_s, lat_n) -> tuple[int, int]:
        ppd = TILE_PX / tile_deg(z)
        return (max(2, min(self.max_px, round((lon_e - lon_w) * ppd))),
                max(2, min(self.max_px, round((lat_n - lat_s) * ppd))))

    def render(self, level: str, edges: tuple[float, float, float, float]):
        """Return the RGB texture for `level` ('context' or 'corridor') over (lon_w, lon_e, lat_s, lat_n)."""
        from PIL import Image

        lon_w, lon_e, lat_s, lat_n = edges
        ctx_z = choose_zoom(lon_w, lon_e, lat_s, lat_n, self.context_layer["max_zoom"], self.max_px)
        top_z = ctx_z
        if level == "corridor":
            hi_z = choose_zoom(lon_w, lon_e, lat_s, lat_n, self.corridor_layer["max_zoom"], self.max_px)
            top_z = max(ctx_z, hi_z)
        size = self._out_size(top_z, *edges)

        ctx, c_counts = self.mosaic(self.context_layer, ctx_z, *edges, size)
        base = np.asarray(ctx, dtype=np.float32)
        have = c_counts["ok"] > 0
        if level == "corridor":
            hi, h_counts = self.mosaic(self.corridor_layer, hi_z, *edges, size)
            if h_counts["ok"] > 0:
                have = True
                base = self._composite(base, np.asarray(hi, dtype=np.float32))
        if not have:
            raise TextureUnavailable(f"no {level} imagery could be fetched from {self.provider}")

        alpha = base[..., 3:4] / 255.0
        rgb = base[..., :3] * alpha + np.asarray(BASE_RGB, np.float32) * (1.0 - alpha)
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")

    @staticmethod
    def _composite(ctx: np.ndarray, hi: np.ndarray) -> np.ndarray:
        """Tone-match HiRISE to CTX where both exist, then alpha-blend HiRISE over CTX."""
        both = (ctx[..., 3] > 0) & (hi[..., 3] > 0)
        if both.sum() > 100:
            hi = hi.copy()
            for c in range(3):
                h, b = hi[..., c][both], ctx[..., c][both]
                scale = float(b.std()) / max(float(h.std()), 1e-3)
                hi[..., c] = (hi[..., c] - float(h.mean())) * scale + float(b.mean())
        a = hi[..., 3:4] / 255.0
        out = ctx.copy()
        out[..., :3] = hi[..., :3] * a + ctx[..., :3] * (1.0 - a)
        out[..., 3] = np.maximum(ctx[..., 3], hi[..., 3])
        return out

    # ----------------------------------------------------------------------------------- cache
    def path_for(self, mission_id: str, level: str) -> Path:
        return self.out_dir / f"{mission_id}_{level}.jpg"

    def _lock(self, path: Path) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(path, threading.Lock())

    def ensure(self, mission_id: str, level: str, edges: tuple[float, float, float, float]) -> Path:
        """Build the texture if needed and return its path. Blocking: call it from an executor."""
        if level not in ("context", "corridor"):
            raise ValueError(f"unknown texture level {level!r}")
        path = self.path_for(mission_id, level)
        with self._lock(path):
            if path.exists():
                return path
            now = time.monotonic()
            for key in (path, self.provider):   # this texture, or the provider as a whole
                failed = self._failed_at.get(key)
                if failed is not None and now - failed < FAILURE_MEMO_S:
                    raise TextureUnavailable(f"{level} imagery unavailable (recent failure)")
            t0 = time.perf_counter()
            try:
                img = self.render(level, edges)
            except TextureUnavailable as exc:
                self._failed_at[path] = time.monotonic()
                if "unreachable" in str(exc):
                    self._failed_at[self.provider] = time.monotonic()
                raise
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=JPEG_QUALITY)
            part.write_bytes(buf.getvalue())
            os.replace(part, path)
            self._failed_at.pop(path, None)
            log.info("texture %s %dx%d built in %.1fs", path.name, img.width, img.height,
                     time.perf_counter() - t0)
            return path
