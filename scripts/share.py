"""Build and share M3 + the terrain planner using `pixi run share`.

Reuses healthy existing services, otherwise starts them. Only processes started
by this command are stopped on exit. The optional M4/M5 driving sim is separate.
Cloudflare setup follows the old Mars_Demo feat/thanh launcher.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"
LOCAL = "http://127.0.0.1:4173"
CF_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"


def executable(name: str) -> str:
    result = shutil.which(name)
    if not result:
        raise RuntimeError(f"{name} is missing. Run this command with Pixi.")
    return result


def cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    windows = os.name == "nt"
    target = ROOT / ".tools" / ("cloudflared.exe" if windows else "cloudflared")
    if target.is_file():
        return str(target)
    if platform.system() == "Darwin":
        raise RuntimeError("Install cloudflared with `brew install cloudflared`, then run `pixi run share`.")
    machine = platform.machine().lower()
    if machine not in {"arm64", "aarch64", "amd64", "x86_64"}:
        raise RuntimeError("Install cloudflared for your platform and add it to PATH.")
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    asset = f"cloudflared-windows-{arch}.exe" if windows else f"cloudflared-linux-{arch}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    print("[setup] Downloading cloudflared from Cloudflare's official release…", flush=True)
    with urllib.request.urlopen(CF_BASE + asset, timeout=120) as response, temporary.open("wb") as destination:
        shutil.copyfileobj(response, destination)
    temporary.chmod(0o755)
    temporary.replace(target)
    return str(target)


def port_busy(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def healthy(url: str, kind: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "mars-odyssey-launcher"})
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                return False
            content = response.read()
        if kind == "api":
            data = json.loads(content)
            return isinstance(data, dict) and data.get("status") == "ok" and data.get("res_m") == 5 and isinstance(data.get("grid_shape"), list)
        return b"MARS ODYSSEY" in content and b'type="module"' in content
    except (OSError, ValueError, urllib.error.URLError):
        return False


def web_needs_build() -> bool:
    index = WEB / "dist/index.html"
    if not index.exists():
        return True
    stamp = index.stat().st_mtime_ns
    paths = [WEB / name for name in ("src", "public", "index.html", "vite.config.ts", "package.json", "package-lock.json")]
    for path in paths:
        files = path.rglob("*") if path.is_dir() else [path]
        if any(p.is_file() and p.stat().st_mtime_ns > stamp for p in files):
            return True
    return False


class Processes:
    def __init__(self) -> None:
        self.children: list[tuple[str, subprocess.Popen]] = []
        self.lines: queue.Queue[tuple[str, str]] = queue.Queue()
        self.public_url: str | None = None
        self.registered = False

    def start(self, name: str, command: list[str], cwd: Path = ROOT) -> subprocess.Popen:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1, **options)
        self.children.append((name, process))
        def read() -> None:
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    self.lines.put((name, line.rstrip()))
        threading.Thread(target=read, daemon=True).start()
        return process

    def pump(self) -> None:
        while True:
            try:
                name, line = self.lines.get_nowait()
            except queue.Empty:
                break
            if name == "tunnel":
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if match:
                    self.public_url = match.group(0)
                if "Registered tunnel connection" in line:
                    self.registered = True
                if not re.search(r"\b(WRN|ERR|FTL)\b", line):
                    continue
            print(f"[{name}] {line}", flush=True)
        for name, process in self.children:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with code {process.returncode}.")

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if os.name == "nt":
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                return
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()

    def run(self, name: str, command: list[str], cwd: Path = ROOT) -> None:
        process = self.start(name, command, cwd)
        self.children.remove((name, process))
        try:
            while process.poll() is None:
                self.pump(); time.sleep(.1)
            self.pump()
            if process.returncode:
                raise RuntimeError(f"{name} failed with code {process.returncode}.")
        finally:
            if process.poll() is None:
                self.stop(process)

    def close(self) -> None:
        for _, process in reversed(self.children):
            self.stop(process)


def wait_ready(processes: Processes, url: str, kind: str, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        processes.pump()
        if healthy(url, kind):
            return
        time.sleep(.3)
    raise RuntimeError(f"Timed out waiting for {url}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="Run locally without Cloudflare")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    processes = Processes()
    try:
        npm, npx = executable("npm"), executable("npx")
        api_exists, web_exists = port_busy(8000), port_busy(4173)
        for exists, url, kind in ((api_exists, "http://127.0.0.1:8000/api/health", "api"), (web_exists, LOCAL, "web")):
            if exists and not healthy(url, kind):
                raise RuntimeError(f"An incompatible service occupies {url}. Stop that service before sharing.")
        if not api_exists:
            required = [ROOT / f"data/processed/{name}_5m.tif" for name in ("elevation", "slope", "roughness")]
            required.append(ROOT / "data/analysis/hazard_5m.tif")
            if any(not path.exists() for path in required):
                raise RuntimeError("Terrain products are missing. Run `pixi run data` once, then `pixi run share`.")
        marker, lock = WEB / "node_modules/.package-lock.json", WEB / "package-lock.json"
        if not marker.exists() or marker.stat().st_mtime_ns < lock.stat().st_mtime_ns:
            processes.run("install", [npm, "install", "--no-audit", "--no-fund"], WEB)
        if web_needs_build():
            processes.run("build", [npm, "run", "build"], WEB)
        if api_exists:
            print("[api] Reusing the existing Planner on :8000.")
        else:
            processes.start("api", [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", "8000", "--log-level", "warning"])
        if web_exists:
            print("[web] Reusing the existing preview on :4173.")
        else:
            processes.start("web", [npx, "vite", "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], WEB)
        wait_ready(processes, "http://127.0.0.1:8000/api/health", "api")
        wait_ready(processes, LOCAL, "web")
        public = None
        if not args.local:
            processes.start("tunnel", [cloudflared(), "tunnel", "--no-autoupdate", "--url", LOCAL])
            deadline = time.monotonic() + 90
            while not (processes.public_url and processes.registered):
                processes.pump()
                if time.monotonic() > deadline:
                    raise RuntimeError("Cloudflare tunnel did not register within 90 seconds.")
                time.sleep(.25)
            public = processes.public_url
            wait_ready(processes, public, "web", seconds=90)
            wait_ready(processes, public + "/api/health", "api", seconds=30)
        print("\nMARS ODYSSEY is ready")
        if public:
            print(f"PUBLIC    {public}")
            print("Share this URL. It works while this command and its services keep running.")
        print(f"LOCAL     {LOCAL}")
        print("Ctrl+C stops this tunnel and any services started by this command.\n")
        if not args.no_browser:
            webbrowser.open(public or LOCAL)
        while True:
            processes.pump(); time.sleep(.25)
    except KeyboardInterrupt:
        print("\nStopping…")
        return 0
    except (RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        processes.close()


if __name__ == "__main__":
    raise SystemExit(main())
