"""Check that the running Python environment is exactly the one `pixi.lock` describes.

Why: the physics must behave the same on every machine. `pixi.lock` pins every conda package to an
exact file (version *and* build) per platform, but nothing forces anyone to use it: a re-solved
environment, a pip-installed pybullet or another project's environment all run the sim with
different binaries (and, for PyBullet, potentially different collision behaviour). This compares
the packages actually installed in ``sys.prefix`` with the lockfile entry for this platform.

Run: `pixi run check-env` (also run automatically by `pixi run demo`).
Fix a mismatch: `pixi install --locked`, or `pixi reinstall` if files were changed by hand.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCKFILE = ROOT / "pixi.lock"
EXPECTED_PREFIX = ROOT / ".pixi" / "envs" / "default"
CRITICAL = ("pybullet", "python", "numpy", "aiohttp", "scipy", "rasterio", "gdal")


def conda_subdir() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win-64"
    if sys.platform == "darwin":
        return "osx-arm64" if machine in ("arm64", "aarch64") else "osx-64"
    return "linux-aarch64" if machine in ("arm64", "aarch64") else "linux-64"


def _name_of(filename: str) -> str:
    """`pybullet-3.25-py311h3e17986_5.conda` -> `pybullet` (name is everything before version-build)."""
    stem = filename.removesuffix(".conda").removesuffix(".tar.bz2")
    return stem.rsplit("-", 2)[0]


@dataclass
class EnvReport:
    subdir: str
    prefix: Path
    in_repo_env: bool
    locked: dict[str, str] = field(default_factory=dict)     # name -> file
    installed: dict[str, str] = field(default_factory=dict)  # name -> file
    error: str | None = None

    @property
    def missing(self) -> list[str]:
        return sorted(n for n in self.locked if n not in self.installed)

    @property
    def extra(self) -> list[str]:
        return sorted(n for n in self.installed if n not in self.locked)

    @property
    def different(self) -> list[tuple[str, str, str]]:
        return sorted((n, self.locked[n], self.installed[n]) for n in self.locked
                      if n in self.installed and self.locked[n] != self.installed[n])

    @property
    def ok(self) -> bool:
        return self.error is None and not (self.missing or self.extra or self.different)

    def version_of(self, name: str) -> str | None:
        f = self.installed.get(name)
        return f.removesuffix(".conda").removesuffix(".tar.bz2")[len(name) + 1:] if f else None

    def summary(self) -> str:
        if self.error:
            return self.error
        if self.ok:
            return f"environment matches pixi.lock exactly ({len(self.installed)} packages, {self.subdir})"
        lines = [f"environment does NOT match pixi.lock ({self.subdir}, {self.prefix}):"]
        crit = [d for d in self.different if d[0] in CRITICAL] + \
               [(n, self.locked[n], "-") for n in self.missing if n in CRITICAL]
        for n, want, have in crit:
            lines.append(f"  ! {n}: locked {want}, installed {have}")
        lines.append(f"  {len(self.different)} different, {len(self.missing)} missing, {len(self.extra)} extra")
        for n, want, have in [d for d in self.different if d[0] not in CRITICAL][:8]:
            lines.append(f"    {n}: locked {want}, installed {have}")
        if self.extra[:8]:
            lines.append("    extra: " + ", ".join(self.extra[:8]) + (" ..." if len(self.extra) > 8 else ""))
        return "\n".join(lines)


def check(env_name: str = "default") -> EnvReport:
    subdir = conda_subdir()
    prefix = Path(sys.prefix).resolve()
    rep = EnvReport(subdir=subdir, prefix=prefix,
                    in_repo_env=prefix == EXPECTED_PREFIX.resolve())
    meta = prefix / "conda-meta"
    if not meta.is_dir():
        rep.error = (f"not running in a pixi/conda environment ({sys.executable}); "
                     "start it with `pixi run ...` from the repo root")
        return rep
    try:
        import yaml
        lock = yaml.safe_load(LOCKFILE.read_text(encoding="utf-8"))
        entries = lock["environments"][env_name]["packages"][subdir]
    except FileNotFoundError:
        rep.error = f"{LOCKFILE} not found"
        return rep
    except (KeyError, TypeError):
        rep.error = f"pixi.lock has no '{env_name}' environment for {subdir}"
        return rep
    for e in entries:
        url = e.get("conda") if isinstance(e, dict) else None
        if url:                                   # pypi entries (none today) are not in conda-meta
            fn = url.rsplit("/", 1)[-1]
            rep.locked[_name_of(fn)] = fn
    for f in meta.glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fn = rec.get("fn") or (rec.get("url") or "").rsplit("/", 1)[-1]
        if fn:
            rep.installed[_name_of(fn)] = fn
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify the environment matches pixi.lock exactly")
    ap.add_argument("--warn-only", action="store_true", help="report but exit 0")
    args = ap.parse_args(argv)
    rep = check()
    print(rep.summary())
    if not rep.in_repo_env and rep.error is None:
        print(f"note: running from {rep.prefix}, not this repo's {EXPECTED_PREFIX}")
    if not rep.ok:
        print("fix: `pixi install --locked` (or `pixi reinstall`), then run through `pixi run`")
    return 0 if rep.ok or args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
