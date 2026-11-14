"""Record the 60 s Vietnamese promo of the live app (Playwright screen recording).

Captions and the intro/outro cards are injected into the page, so the recording already
contains them; build.sh then trims it and lays the voice-over on top.

usage: python tools/video/record.py <durations.json> <out_dir> [app_url]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

DUR = json.loads(Path(sys.argv[1]).read_text())
OUT = Path(sys.argv[2])
URL = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:4173/"
CHROME = str(next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
ARGS = ["--no-sandbox", "--disable-gpu", "--enable-features=NetworkServiceInProcess2",
        "--disable-features=NetworkServiceSandbox"]
PAD = 0.6  # silence after each spoken line
W, H = 1920, 1080

OVERLAY_JS = """
() => {
  const css = `
  #mo-cap{position:fixed;left:50%;bottom:78px;transform:translateX(-50%);z-index:99999;max-width:1180px;
    padding:14px 26px;border-radius:16px;background:rgba(6,9,14,.82);backdrop-filter:blur(10px);
    border:1px solid rgba(255,106,61,.45);box-shadow:0 12px 40px rgba(0,0,0,.6);
    font:600 30px/1.3 Inter,sans-serif;color:#fff;text-align:center;transition:opacity .35s, transform .35s}
  #mo-cap.hide{opacity:0;transform:translateX(-50%) translateY(10px)}
  #mo-cap .k{font:600 13px/1 'JetBrains Mono',monospace;letter-spacing:.2em;color:#ff8a5c;margin-bottom:8px;text-transform:uppercase}
  #mo-step{position:fixed;left:50%;bottom:150px;transform:translateX(-50%);z-index:99999;
    font:600 13px/1 'JetBrains Mono',monospace;letter-spacing:.2em;color:#ff8a5c;text-transform:uppercase;
    text-shadow:0 2px 8px #000;transition:opacity .35s}
  #mo-card{position:fixed;inset:0;z-index:100000;display:grid;place-items:center;
    background:radial-gradient(ellipse at 50% 40%,rgba(255,106,61,.20),rgba(4,6,10,.94) 60%);
    transition:opacity .8s}
  #mo-card.hide{opacity:0;pointer-events:none}
  #mo-card .k{font:600 15px 'JetBrains Mono',monospace;letter-spacing:.3em;color:#ff8a5c;text-align:center}
  #mo-card h1{margin:.25em 0 .1em;font:700 120px/1 'Space Grotesk',sans-serif;letter-spacing:-.02em;color:#fff;text-align:center}
  #mo-card h1 span{background:linear-gradient(90deg,#ff6a3d,#ffb08f);-webkit-background-clip:text;color:transparent}
  #mo-card p{font:500 32px/1.4 Inter,sans-serif;color:#c9d3df;text-align:center;margin:0}
  #mo-card .s{margin-top:34px;font:500 18px Inter,sans-serif;color:#8494a7;text-align:center}
  #mo-card .planet{width:120px;height:120px;margin:0 auto 28px;border-radius:50%;
    background:radial-gradient(circle at 35% 30%,#ffb08f,#ff6a3d 35%,#7a2a10 75%);
    box-shadow:0 0 80px rgba(255,106,61,.55)}`;
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  const cap = document.createElement('div'); cap.id = 'mo-cap'; cap.className = 'hide'; document.body.appendChild(cap);
  const step = document.createElement('div'); step.id = 'mo-step'; document.body.appendChild(step);
  const card = document.createElement('div'); card.id = 'mo-card'; card.className = 'hide'; document.body.appendChild(card);
  window.moCap = (html, stepTxt) => { cap.innerHTML = (stepTxt ? `<div class='k'>${stepTxt}</div>` : '') + html;
    cap.classList.toggle('hide', !html); step.style.opacity = 0; };
  window.moCard = (html) => { if (html) { card.innerHTML = html; card.classList.remove('hide') } else card.classList.add('hide') };
}
"""

INTRO = """<div><div class='planet'></div><div class='k'>NASA Space Apps Challenge 2026</div>
<h1>MARS <span>ODYSSEY</span></h1><p>Bản đồ thông minh giúp phi hành gia đi bộ an toàn trên Sao Hỏa</p></div>"""
OUTRO = """<div><div class='planet'></div><h1>MARS <span>ODYSSEY</span></h1>
<p>Khám phá thông minh hơn · Di chuyển an toàn hơn</p>
<div class='s'>Dữ liệu: NASA · USGS · JPL-Caltech · MRO HiRISE · Mars Odyssey THEMIS · Mars 2020 Perseverance</div></div>"""


async def warm_tiles(ctx) -> None:
    """Visit every camera position once so NASA Trek tiles are in the HTTP cache before recording."""
    w = await ctx.new_page()
    btn = lambda name: w.get_by_role("button", name=name, exact=True)
    await w.goto(URL, wait_until="load"); await w.wait_for_timeout(4000)
    await w.get_by_text("Delta front").click(); await w.wait_for_timeout(6000)
    await btn("Zoom in").click(); await w.wait_for_timeout(6000)
    await btn("Reset view").click(); await w.wait_for_timeout(4000)
    await btn("Science").click(); await w.wait_for_timeout(500)
    await w.get_by_text("Sapphire Canyon").first.click(); await w.wait_for_timeout(7000)
    await btn("Mission Planner").click(); await w.wait_for_timeout(500)
    await w.get_by_text("Demo scenario").click()
    await w.get_by_text("Recommended").first.wait_for(timeout=20000); await w.wait_for_timeout(7000)
    await w.close()


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    seg = {d["id"]: d for d in DUR}
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=ARGS)
        ctx = await b.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT),
                                  record_video_size={"width": W, "height": H})
        await ctx.add_init_script("try{localStorage.setItem('mo.briefing.seen','1')}catch(e){}")
        await warm_tiles(ctx)
        pg = await ctx.new_page()
        t_video = time.monotonic()
        await pg.goto(URL, wait_until="load")
        await pg.wait_for_timeout(3500)            # tiles + data
        await pg.evaluate(OVERLAY_JS)
        await pg.evaluate("moCard(arguments[0])" if False else f"moCard({json.dumps(INTRO)})")
        await pg.wait_for_timeout(900)
        marks: list[dict] = []

        async def scene(sid: str, step: str, action=None):
            s = seg[sid]
            t = time.monotonic() - t_video
            marks.append({"id": sid, "t": round(t, 3)})
            if sid not in ("intro", "outro"):
                await pg.evaluate(f"moCap({json.dumps(s['caption'])}, {json.dumps(step)})")
            t_end = time.monotonic() + s["dur"] + PAD
            if action:
                await action()
            rest = t_end - time.monotonic()
            if rest > 0:
                await pg.wait_for_timeout(rest * 1000)

        btn = lambda name: pg.get_by_role("button", name=name, exact=True)

        # 1 intro: title card over the map
        async def a_intro():
            await pg.wait_for_timeout(5200)
            await pg.evaluate("moCard(null)")
        await scene("intro", "", a_intro)

        # 2 explore: fly into the delta front on HiRISE
        async def a_explore():
            await pg.get_by_text("Delta front").click()
            await pg.wait_for_timeout(3800)
            await btn("Zoom in").click()
        await scene("explore", "01 · Khám phá", a_explore)

        # 3 layers: slope, then hazard, zoom out to see the whole area
        async def a_layers():
            await btn("Data Layers").click()
            await pg.wait_for_timeout(900)
            await btn("Reset view").click()
            await pg.wait_for_timeout(1800)
            await btn("Slope").click()
            await pg.wait_for_timeout(3000)
            await btn("Slope").click()
            await btn("Hazard").click()
        await scene("layers", "02 · Lớp dữ liệu", a_layers)

        # 4 safety tab
        await scene("safety", "03 · An toàn", lambda: btn("Safety").click())

        # 5 science: Cheyava Falls sample + rover images
        async def a_science():
            await btn("Hazard").click()          # back to plain imagery for the close-up
            await btn("Science").click()
            await pg.wait_for_timeout(500)
            await pg.get_by_text("Sapphire Canyon").first.click()
            await pg.wait_for_timeout(2600)
            await pg.locator("aside").evaluate("el => el.scrollTo({top: el.scrollHeight, behavior: 'smooth'})")
        await scene("science", "04 · Khoa học", a_science)

        # 6 mission: demo route
        async def a_mission():
            await btn("Mission Planner").click()
            await pg.wait_for_timeout(500)
            await pg.get_by_text("Demo scenario").click()
            await pg.get_by_text("Recommended").first.wait_for(timeout=15000)
            await pg.wait_for_timeout(1500)
            await pg.locator("aside").evaluate("el => el.scrollTo({top: 360, behavior: 'smooth'})")
        await scene("mission", "05 · Lập kế hoạch", a_mission)

        # 7 profile: sweep the cursor along the elevation chart
        async def a_profile():
            box = await pg.locator(".recharts-surface").first.bounding_box()
            if box:
                y = box["y"] + box["height"] * 0.5
                for i in range(40):
                    await pg.mouse.move(box["x"] + 50 + (box["width"] - 60) * i / 39, y)
                    await pg.wait_for_timeout(80)
        await scene("profile", "06 · Mặt cắt độ cao", a_profile)

        # 8 outro card
        async def a_outro():
            await pg.evaluate("moCap('', '')")
            await pg.evaluate(f"moCard({json.dumps(OUTRO)})")
        await scene("outro", "", a_outro)
        await pg.wait_for_timeout(600)

        marks.append({"id": "end", "t": round(time.monotonic() - t_video, 3)})
        video = await pg.video.path()
        await ctx.close()
        await b.close()
    (OUT / "marks.json").write_text(json.dumps({"video": str(video), "marks": marks}, indent=1))
    print(json.dumps(marks))


asyncio.run(main())
