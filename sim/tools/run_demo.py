"""One-command launcher for MARS ODYSSEY (`pixi run demo`, `pixi run share`).

Why one process: a demo needs the data pipeline, the 3D assets, a production web build and three
servers (planner API :8000, sim server :8100, web preview :4173). Doing each step only when its
inputs changed makes the second start take seconds, and owning every child process means Ctrl+C
(or any child dying) tears the whole tree down instead of leaving orphans on the ports.

Optional `--share` exposes :4173 through a Cloudflare quick tunnel; the Vite preview already proxies
/api and /sim, so one public origin serves everything.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FRONTEND = ROOT / "frontend"
PROCESSED = ROOT / "data" / "processed"
ANALYSIS = ROOT / "data" / "analysis"
DTM = ROOT / "data" / "raw" / "hirise_dtm" / "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
WEB_DATA = FRONTEND / "public" / "data"
TOOLS_DIR = ROOT / "sim" / ".tools"

PORT_API, PORT_SIM, PORT_WEB = 8000, 8100, 4173
CORE_SCRIPTS = ["download_data.py", "validate_data.py", "process_terrain.py",
                "generate_hazard.py", "render_layers.py", "build_science_targets.py"]
ENV_SCRIPTS = [("download_env_data.py", ["--skip-large"]), ("process_geology.py", []), ("generate_hazard.py", []),
               ("render_layers.py", []), ("build_climate.py", []), ("fetch_space_weather.py", [])]
ENV_STEP_TIMEOUT_S = 300
HEALTH_TIMEOUT_S = 180
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-{}"

IS_WIN = os.name == "nt"
C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "red": "\033[31m", "green": "\033[32m",
     "yellow": "\033[33m", "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m"}
ACCESS_LOG = re.compile(r'("(GET|POST|PUT|HEAD|OPTIONS|DELETE) [^"]*" \d{3})|(\bHTTP/1\.[01]" \d{3}\b)|INFO:\s+\d+\.\d+\.\d+\.\d+:\d+ - ')


# ---------------------------------------------------------------- console
def setup_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    if IS_WIN:
        os.system("")  # enables VT100 escape processing on Windows 10+ consoles


def say(msg: str, color: str = "cyan") -> None:
    print(f"{C[color]}▸{C['reset']} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{C['yellow']}WARNING: {msg}{C['reset']}", flush=True)


def die(msg: str) -> None:
    print(f"{C['red']}ERROR: {msg}{C['reset']}", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- helpers
def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_script(name: str, args: list[str] | None = None, timeout: float | None = None) -> int:
    cmd = [sys.executable, str(SCRIPTS / name), *(args or [])]
    say(f"{C['dim']}{' '.join(['python', 'scripts/' + name, *(args or [])])}{C['reset']}")
    try:
        return subprocess.run(cmd, cwd=ROOT, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        warn(f"{name} timed out after {timeout:.0f} s")
        return 124


def which(cmd: str) -> str:
    path = shutil.which(cmd)
    if not path:
        die(f"'{cmd}' not found on PATH (run inside `pixi run`, which provides nodejs)")
    return path  # type: ignore[return-value]


def newest_mtime(paths: list[Path], skip: tuple[str, ...] = ("node_modules", "dist")) -> float:
    newest = 0.0
    for p in paths:
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
        elif p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in skip]
                for f in filenames:
                    try:
                        newest = max(newest, os.stat(os.path.join(dirpath, f)).st_mtime)
                    except OSError:
                        pass
    return newest


def http_ok(url: str, timeout: float = 3.0) -> int | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "run_demo"}), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------- steps
def step_data(env_data: bool) -> None:
    if (PROCESSED / "elevation_5m.tif").exists() and (ANALYSIS / "hazard_5m.tif").exists():
        say("data: C2 rasters present, skipping the pipeline")
    else:
        scripts = CORE_SCRIPTS[1:] if DTM.exists() else CORE_SCRIPTS
        say("data: " + ("raw DTM present, deriving layers" if DTM.exists() else "downloading sources (~1.8 GB DTM)"))
        for s in scripts:
            if run_script(s) != 0:
                die(f"data pipeline step {s} failed (see output above; `pixi run data` reruns it)")
    if not env_data:
        return
    if (PROCESSED / "geologic_units_5m.tif").exists() and (WEB_DATA / "climate").is_dir():
        return
    say("data-env: environment & geology track (optional, non-fatal)")
    try:
        for s, args in ENV_SCRIPTS:
            if not (SCRIPTS / s).exists():
                raise RuntimeError(f"scripts/{s} missing")
            rc = run_script(s, args, timeout=ENV_STEP_TIMEOUT_S)
            if rc != 0:
                raise RuntimeError(f"{s} exited with {rc}")
    except Exception as exc:  # noqa: BLE001 - this step must never block the demo
        warn(f"data-env skipped ({exc}); continuing with the v1.0 data")


def step_assets() -> None:
    glb = ROOT / "sim" / "rover" / "model" / "perseverance.glb"
    three = ROOT / "sim" / "web" / "vendor" / "three"
    if glb.exists() and three.is_dir() and any(three.iterdir()):
        return
    say("assets: fetching the rover model and three.js (M4 fetch_assets)")
    rc = subprocess.run([sys.executable, "-m", "sim.tools.fetch_assets"], cwd=ROOT).returncode
    if rc != 0:
        die("sim.tools.fetch_assets failed")


def step_web() -> None:
    if not (FRONTEND / "package.json").exists():
        die("frontend/package.json missing (module M3 not present)")
    npm = which("npm")
    nm, lock = FRONTEND / "node_modules", FRONTEND / "package-lock.json"
    if not nm.exists() or (lock.exists() and nm.stat().st_mtime < lock.stat().st_mtime):
        say("web: npm install")
        if subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND).returncode != 0:
            die("npm install failed")
        nm.touch()
    dist = FRONTEND / "dist" / "index.html"
    sources = [FRONTEND / p for p in ("src", "public", "index.html", "package.json", "package-lock.json",
                                      "vite.config.ts", "vite.config.js", "tsconfig.json", "tsconfig.app.json")]
    if not dist.exists() or newest_mtime(sources) > dist.stat().st_mtime:
        say("web: npm run build")
        if subprocess.run([npm, "run", "build"], cwd=FRONTEND).returncode != 0:
            die("npm run build failed")


# ---------------------------------------------------------------- processes
STOP_GRACE_S = 8.0  # how long servers get to shut down cleanly before they are force-killed


class _KillOnCloseJob:
    """Windows Job Object that kills every process in it when the launcher dies.

    Why: the graceful path below only runs if the launcher itself survives. Closing the terminal,
    killing pixi, or a crash would otherwise leave the servers running as orphans holding
    :8000/:8100/:4173. The OS closes the job handle when the launcher exits, by any route, and
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE then terminates every server and its descendants.
    """

    _EXTENDED_LIMIT_INFO = 9
    _KILL_ON_JOB_CLOSE = 0x2000
    CREATE_SUSPENDED = 0x00000004

    def __init__(self) -> None:
        self.handle = None
        if not IS_WIN:
            return
        try:
            import ctypes
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("Read", "Write", "Other", "ReadB", "WriteB", "OtherB")]

            class Extended(ctypes.Structure):
                _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            ntdll = ctypes.WinDLL("ntdll")
            ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
            job = k32.CreateJobObjectW(None, None)
            info = Extended()
            info.Basic.LimitFlags = self._KILL_ON_JOB_CLOSE
            if job and k32.SetInformationJobObject(job, self._EXTENDED_LIMIT_INFO, ctypes.byref(info),
                                                   ctypes.sizeof(info)):
                self.handle, self._k32, self._ntdll = job, k32, ntdll
        except (OSError, AttributeError):
            self.handle = None

    def adopt(self, proc: subprocess.Popen) -> None:
        """Assign a child started with CREATE_SUSPENDED to the job, then let it run.

        Starting it suspended means it cannot spawn grandchildren (npx -> node) before it is in the
        job; processes created from inside a job are in it too.
        """
        handle = int(proc._handle)  # type: ignore[attr-defined]
        try:
            if self.handle is not None and not self._k32.AssignProcessToJobObject(self.handle, handle):
                warn(f"could not tie pid {proc.pid} to the launcher; it may outlive a hard kill")
        finally:
            self._ntdll.NtResumeProcess(handle)


_JOB: _KillOnCloseJob | None = None


def _job() -> _KillOnCloseJob:
    global _JOB
    if _JOB is None:
        _JOB = _KillOnCloseJob()
    return _JOB


def _die_with_parent() -> None:  # pragma: no cover - runs in the forked child (Linux only)
    """POSIX counterpart of the job: SIGTERM this child if the launcher dies."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except OSError:
        pass  # macOS has no PDEATHSIG; the graceful path still applies


class Child:
    def __init__(self, tag: str, color: str, cmd: list[str], cwd: Path, filter_access: bool = True,
                 only: re.Pattern[str] | None = None):
        self.tag, self.color, self.filter_access, self.only = tag, color, filter_access, only
        kw: dict = {"cwd": cwd, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "bufsize": 1,
                    "text": True, "encoding": "utf-8", "errors": "replace",
                    "env": {**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"}}
        job = _job() if IS_WIN else None
        if IS_WIN:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            if job is not None and job.handle is not None:
                flags |= _KillOnCloseJob.CREATE_SUSPENDED
            kw["creationflags"] = flags
        else:
            kw["start_new_session"] = True
            if sys.platform.startswith("linux"):
                kw["preexec_fn"] = _die_with_parent
        self.proc = subprocess.Popen(cmd, **kw)
        if job is not None and job.handle is not None:
            job.adopt(self.proc)
        self.lines: list[str] = []
        self.listeners: list = []
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()

    def _pump(self, stream: IO[str]) -> None:
        for line in stream:
            line = line.rstrip("\r\n")
            for fn in self.listeners:
                fn(line)
            if not line.strip():
                continue
            if self.filter_access and ACCESS_LOG.search(line):
                continue
            if self.only is not None and not self.only.search(line):
                continue
            print(f"{C[self.color]}[{self.tag}]{C['reset']} {line}", flush=True)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request_stop(self) -> None:
        """Ask politely: CTRL_BREAK to the child's process group (Windows) or SIGTERM (POSIX)."""
        if not self.alive():
            return
        try:
            if IS_WIN:
                os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # no shared console, or already gone: force_kill handles it

    def force_kill(self) -> None:
        if not self.alive():
            return
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=5)
        except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
            pass


def stop_all(children: list["Child"], grace_s: float = STOP_GRACE_S) -> None:
    """Stop every child: polite signal to all at once, one shared grace period, then force."""
    live = [c for c in children if c.alive()]
    for c in live:
        c.request_stop()
    deadline = time.monotonic() + grace_s
    for c in live:
        try:
            c.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    forced = [c.tag for c in live if c.alive()]
    for c in live:
        c.force_kill()
    clean = [c.tag for c in live if c.tag not in forced]
    if clean:
        say(f"stopped cleanly: {', '.join(clean)}", "green")
    if forced:
        warn(f"force-killed after {grace_s:.0f} s: {', '.join(forced)}")


def start_servers() -> list[Child]:
    py = sys.executable
    # Run Vite through node directly: on Windows `npx` is a .cmd batch file, and stopping it makes
    # cmd.exe ask "Terminate batch job (Y/N)?", which blocks a clean shutdown.
    vite_js = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    web_cmd = ([which("node"), str(vite_js)] if vite_js.exists() else [which("npx"), "vite"]) + \
        ["preview", "--host", "127.0.0.1", "--port", str(PORT_WEB), "--strictPort"]
    return [
        Child("api", "blue", [py, "-m", "uvicorn", "backend.main:app", "--port", str(PORT_API), "--log-level", "warning"], ROOT),
        Child("sim", "magenta", [py, "-m", "sim.server.sim_server", "--port", str(PORT_SIM)], ROOT),
        Child("web", "green", web_cmd, FRONTEND),
    ]


def wait_healthy(children: list[Child]) -> None:
    checks = {"planner API": f"http://127.0.0.1:{PORT_API}/api/health",
              "sim server": f"http://127.0.0.1:{PORT_SIM}/sim/config",
              "web app": f"http://127.0.0.1:{PORT_WEB}/"}
    pending = dict(checks)
    t0 = time.monotonic()
    while pending:
        for c in children:
            if not c.alive():
                raise RuntimeError(f"{c.tag} exited with code {c.proc.returncode} during start-up")
        for name, url in list(pending.items()):
            if http_ok(url) == 200:
                say(f"{name} ready", "green")
                pending.pop(name)
        if time.monotonic() - t0 > HEALTH_TIMEOUT_S:
            raise RuntimeError("timed out waiting for " + ", ".join(pending))
        time.sleep(0.5)


# ---------------------------------------------------------------- share
def find_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    system = platform.system()
    if system == "Darwin":
        die("cloudflared not found: run `brew install cloudflared`, then `pixi run share` again")
    if system == "Windows":
        name, asset = "cloudflared.exe", "windows-amd64.exe"
    elif system == "Linux":
        name, asset = "cloudflared", "linux-amd64"
    else:
        die(f"--share: no cloudflared download for {system}; install it on PATH")
    dest = TOOLS_DIR / name
    if not dest.exists():
        say(f"share: downloading cloudflared into {dest.relative_to(ROOT)}")
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(CLOUDFLARED_URL.format(asset), timeout=120) as r, open(part, "wb") as fh:
            shutil.copyfileobj(r, fh)
        part.replace(dest)
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(dest)


def start_tunnel() -> tuple[Child, str]:
    exe = find_cloudflared()
    url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    found: dict[str, str] = {}
    registered = threading.Event()
    got_url = threading.Event()

    def listen(line: str) -> None:
        m = url_re.search(line)
        if m and "url" not in found:
            found["url"] = m.group(0)
            got_url.set()
        if "Registered tunnel connection" in line:
            registered.set()

    tun = Child("tunnel", "yellow", [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT_WEB}"],
                ROOT, filter_access=False, only=re.compile(r"\b(WRN|ERR)\b"))
    tun.listeners.append(listen)
    say("share: opening a Cloudflare quick tunnel")
    if not got_url.wait(60) or not tun.alive():
        raise RuntimeError("cloudflared did not report a trycloudflare.com URL")
    if not registered.wait(60):
        raise RuntimeError("cloudflared did not register a tunnel connection")
    url = found["url"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90:
        code = http_ok(url + "/", timeout=8)
        if code is not None and code < 500:
            return tun, url
        time.sleep(2)
    warn("public URL still answers 5xx; it usually becomes reachable within a minute")
    return tun, url


# ---------------------------------------------------------------- main
def banner(public: str | None) -> str:
    local = f"http://127.0.0.1:{PORT_WEB}"
    rows = []
    if public:
        rows += [("Public planner", public + "/"), ("Public 3D sim", public + "/sim/")]
    rows += [("Planner", local + "/"), ("3D sim", local + "/sim/"),
             ("Planner API", f"http://127.0.0.1:{PORT_API}/api/health"), ("Sim server", f"http://127.0.0.1:{PORT_SIM}/sim/")]
    width = max(len(v) for _, v in rows) + 20
    line = "═" * width
    out = [f"{C['bold']}{C['cyan']}╔{line}╗", f"║ {'MARS ODYSSEY is running':<{width - 1}}║", f"╠{line}╣"]
    out += [f"║ {k + ':':<16}{v:<{width - 17}}║" for k, v in rows]
    out += [f"║ {'Ctrl+C stops everything':<{width - 1}}║", f"╚{line}╝{C['reset']}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Prepare and start MARS ODYSSEY on :4173")
    ap.add_argument("--share", action="store_true", help="also open a public Cloudflare quick tunnel")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ap.add_argument("--no-env-data", action="store_true", help="skip the optional environment & geology data")
    args = ap.parse_args(argv)

    busy = [p for p in (PORT_API, PORT_SIM, PORT_WEB) if port_busy(p)]
    if busy:
        die(f"port(s) {', '.join(map(str, busy))} already in use; stop the other process first")

    step_data(env_data=not args.no_env_data)
    step_assets()
    step_web()

    children: list[Child] = []
    stopping = threading.Event()

    def shutdown(*_: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, shutdown)  # type: ignore[attr-defined]

    rc = 0
    try:
        say("starting servers")
        children = start_servers()
        wait_healthy(children)
        public = None
        if args.share:
            tun, public = start_tunnel()
            children.append(tun)
        print(banner(public), flush=True)
        if not args.no_browser:
            webbrowser.open((public or f"http://127.0.0.1:{PORT_WEB}") + "/")
        while not stopping.is_set():
            dead = [c for c in children if not c.alive()]
            if dead:
                warn(f"{dead[0].tag} exited with code {dead[0].proc.returncode}; stopping everything")
                rc = 1
                break
            stopping.wait(0.5)
    except RuntimeError as exc:
        print(f"{C['red']}ERROR: {exc}{C['reset']}", flush=True)
        rc = 1
    finally:
        say("stopping", "yellow")
        stop_all(children)
    return rc


if __name__ == "__main__":
    sys.exit(main())
