"""Streaming, idempotent HTTP download helper shared by download_data.py and download_env_data.py.

Why: the HiRISE DTM is ~1.8 GB and several v1.1 sources are large or flaky. Downloads stream to a
`.part` file and are renamed only when complete, so an interrupted run never leaves a truncated
file that later looks valid. A file whose size already matches the remote Content-Length is
skipped, which makes `pixi run data` cheap to re-run.
"""
from __future__ import annotations

import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

USER_AGENT = "mars-odyssey-m1/1.1 (+https://github.com/; data pipeline)"
CHUNK = 1 << 20


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def remote_size(url: str, timeout: float = 30.0) -> int | None:
    """Content-Length from a HEAD request (falls back to a streamed GET), or None."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    try:
        r = requests.head(url, allow_redirects=True, timeout=timeout, headers=headers)
        if r.ok and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
        with requests.get(url, stream=True, allow_redirects=True, timeout=timeout, headers=headers) as g:
            if g.ok and g.headers.get("Content-Length"):
                return int(g.headers["Content-Length"])
    except requests.RequestException:
        return None
    return None


def download(url: str, dest: Path, timeout: float = 60.0, quiet: bool = False) -> dict[str, Any]:
    """Download `url` to `dest` unless an identical-size copy exists. Returns a provenance record.

    Raises requests.RequestException on network failure (callers decide whether that is fatal).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = remote_size(url)
    skipped = False
    if dest.exists() and size is not None and dest.stat().st_size == size:
        skipped = True
        if not quiet:
            print(f"  = {dest.name}: up to date ({size:,} bytes)", flush=True)
    else:
        part = dest.with_name(dest.name + ".part")
        # identity: byte counts then match Content-Length and the file on disk
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        with requests.get(url, stream=True, allow_redirects=True, timeout=timeout, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0) or size or 0
            done = 0
            t0 = last = time.monotonic()
            with open(part, "wb") as fh:
                for block in r.iter_content(CHUNK):
                    fh.write(block)
                    done += len(block)
                    now = time.monotonic()
                    if not quiet and (now - last > 2.0):
                        last = now
                        rate = done / max(now - t0, 1e-6) / 1e6
                        pct = f"{100 * done / total:5.1f}%" if total else ""
                        sys.stdout.write(f"\r  > {dest.name}: {done / 1e6:,.0f} MB {pct} {rate:.1f} MB/s   ")
                        sys.stdout.flush()
        if total and done != total:
            raise requests.RequestException(f"{url}: got {done} of {total} bytes")
        part.replace(dest)
        if not quiet:
            sys.stdout.write(f"\r  + {dest.name}: {done:,} bytes{' ' * 30}\n")
            sys.stdout.flush()
    return {
        "url": url,
        "size": dest.stat().st_size,
        "sha256": sha256_file(dest),
        "retrieved_utc": utc_now() if not skipped else None,
        "skipped": skipped,
    }


def get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()
