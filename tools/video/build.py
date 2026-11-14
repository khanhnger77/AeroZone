"""Assemble the final MP4: trim the screen recording, speed it up slightly, lay each voice-over
line at the start of its scene, write matching .srt subtitles.

usage: python tools/video/build.py <audio_dir> <rec_dir> <out.mp4>
  audio_dir: <id>.mp3 per scene + durations.json   (from the TTS step)
  rec_dir:   marks.json + the .webm                 (from record.py)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

AUDIO, REC, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
SPEED = 1.05          # video speed-up to land at ~60 s (each scene has 0.6 s slack for the voice)
LEAD = 0.15           # voice starts this long after a scene cut
FADE = 0.6

durs = {d["id"]: d for d in json.loads((AUDIO / "durations.json").read_text())}
rec = json.loads((REC / "marks.json").read_text())
marks = {m["id"]: m["t"] for m in rec["marks"]}
t0, t1 = marks["intro"], marks["end"]
total = (t1 - t0) / SPEED
order = [d for d in durs]  # script order


def at(sid: str) -> float:
    return (marks[sid] - t0) / SPEED + LEAD


inputs, filters, labels = ["-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", rec["video"]], [], []
for k, sid in enumerate(order, start=1):
    inputs += ["-i", str(AUDIO / f"{sid}.mp3")]
    ms = int(at(sid) * 1000)
    filters.append(f"[{k}:a]aresample=48000,adelay={ms}|{ms}[a{k}]")
    labels.append(f"[a{k}]")
filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
               f"afade=t=out:st={total - FADE:.2f}:d={FADE},apad,atrim=0:{total:.3f}[aout]")
filters.append(f"[0:v]setpts=PTS/{SPEED},fps=30,fade=t=in:st=0:d={FADE},"
               f"fade=t=out:st={total - FADE:.2f}:d={FADE},format=yuv420p[vout]")

OUT.parent.mkdir(parents=True, exist_ok=True)
cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
       "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
       "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT)]
subprocess.run(cmd, check=True)


def ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


srt = []
for i, sid in enumerate(order, start=1):
    a = at(sid)
    srt.append(f"{i}\n{ts(a)} --> {ts(a + durs[sid]['dur'])}\n{durs[sid]['voice']}\n")
OUT.with_suffix(".srt").write_text("\n".join(srt), encoding="utf-8")
print(f"{OUT}  duration {total:.1f}s")
