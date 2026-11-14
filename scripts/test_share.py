"""Checks for the Pixi sharing launcher, without opening a public tunnel."""
from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

import pytest

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("mars_share", Path(__file__).with_name("share.py"))
share = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(share)


def test_health_requires_the_actual_web_and_planner():
    class UnhealthyResponse(io.BytesIO):
        status = 503
    with patch.object(share.urllib.request, 'urlopen', return_value=UnhealthyResponse()):
        assert not share.healthy('http://example.invalid', 'web')
    class Response(io.BytesIO):
        status = 200
    api = {'status': 'ok', 'res_m': 5, 'grid_shape': [1007, 2133]}
    with patch.object(share.urllib.request, 'urlopen', return_value=Response(json.dumps(api).encode())):
        assert share.healthy('http://example.invalid', 'api')
    with patch.object(share.urllib.request, 'urlopen', return_value=Response(b'<html>Another app</html>')):
        assert not share.healthy('http://example.invalid', 'web')
    with patch.object(share.urllib.request, 'urlopen', return_value=Response(b'<title>MARS ODYSSEY</title><script type="module"></script>')):
        assert share.healthy('http://example.invalid', 'web')


def test_foreign_busy_port_is_not_published_or_stopped():
    processes = []
    with patch.object(share.signal, 'signal'), patch.object(share, 'executable', side_effect=lambda name: name), patch.object(share, 'port_busy', return_value=True), patch.object(share, 'healthy', return_value=False), patch.object(share.Processes, 'start', side_effect=lambda *args: processes.append(args)):
        assert share.main(['--no-browser']) == 1
    assert not processes


def test_child_exit_is_detected_and_owned_processes_stop():
    processes = share.Processes()
    process = processes.start('test', [sys.executable, '-c', 'import time; time.sleep(60)'])
    processes.close()
    assert process.poll() is not None
    processes = share.Processes()
    process = processes.start('test', [sys.executable, '-c', 'raise SystemExit(7)'])
    process.wait(timeout=10)
    with pytest.raises(RuntimeError, match='exited with code 7'):
        processes.pump()
    processes.close()
