# Online demo: GitHub Pages + Hugging Face Space

A permanent public link that does not depend on anyone's computer. The work is split between
two free hosts:

```
https://<owner>.github.io/<repo>/        GitHub Pages: React app + map data (static files)
        │  HTTPS (fetch) + WSS (live sim state)
        ▼
https://<user>-<space>.hf.space          Hugging Face Docker Space, one port (7860)
   ├─ /api/*   FastAPI planner: routes, point query, raw images, Copilot (key = Space secret)
   ├─ /sim/*   aiohttp + PyBullet sim server and its WebSocket
   └─ /        the same web app, so the Space link alone is also a full demo
```

GitHub Pages can only serve files. The planner, the Copilot and the physics sim need running
Python processes, so they live in the Space. The Space has 2 vCPU, 16 GB RAM and about 50 GB of disk.
The container holds the code plus the ~50 MB of derived terrain rasters, so it starts in seconds
without downloading the 1.8 GB DTM. A free Space sleeps after about 48 h without visitors. The
next visit wakes it in about a minute, and the planner shows "offline" until then.

## How the pieces connect

| Piece | File | What it does |
|---|---|---|
| Backend address | `frontend/src/services/endpoints.ts` | `VITE_BACKEND_URL` (build time) prefixes every `/api` and `/sim` call and the WebSockets. Unset means same origin, as in local runs. |
| Sim iframe | `App.tsx`, `sim/web/js/main.js` | The iframe loads from the backend. The planner passes its own origin as `?parent=`, and both sides post messages only to that origin. |
| CORS | `backend/main.py`, `sim/server/sim_server.py` | The planner API allows any origin. The sim allows the origins in `MO_CORS_ORIGINS` (`*` in the image; none when unset, so local runs are unchanged). |
| Container | `Dockerfile`, `.dockerignore` | Pinned pixi environment, `npm ci`, production build, then `pixi run demo --host 0.0.0.0 --port 7860`. `vite preview` proxies `/api` and `/sim`. |
| Backend upload | `scripts/deploy_space.py` (`pixi run deploy-space`) | Uploads tracked files plus the git-ignored rasters and writes the Space README header. It never uploads `api_key.credentials`. |
| Automation | `.github/workflows/pages.yml`, `space.yml` | On every push to `deploy/web`, the first rebuilds Pages and the second syncs the code to the Space. |

## One-time setup

### 1. Hugging Face Space (backend)

1. Create a free account at <https://huggingface.co>. Then create a token with **Write**
   access at <https://huggingface.co/settings/tokens>.
2. From the repository root, with the data built (`pixi run data` / `pixi run data-env`), run
   the command below. It uploads **git-tracked** files (their current contents) plus the rasters,
   so commit new files first.

   ```bash
   HF_TOKEN=hf_... pixi run deploy-space --space <user>/aerozone --set-key
   ```

   This creates the Space, uploads the code and rasters, and stores the key from
   `api_key.credentials` as the encrypted Space secret `OPENROUTER_API_KEY`.

   On Windows PowerShell, set the token first with `$env:HF_TOKEN="hf_..."`.
3. Wait for the build in the Space's **Logs** tab (about 5 to 10 minutes the first time). Then
   check `https://<user>-aerozone.hf.space/api/health` and `https://<user>-aerozone.hf.space/`.

### 2. GitHub Pages (frontend)

The repository owner does this. Pages is free for public repositories.

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions:**
   - Variable `BACKEND_URL` = `https://<user>-aerozone.hf.space`
   - Variable `HF_SPACE` = `<user>/aerozone`, and secret `HF_TOKEN` = the write token.
     These two are optional; they keep the Space in sync automatically.
3. **Settings → Environments → github-pages → Deployment branches and tags → Add rule:**
   `deploy/web`. By default Pages only accepts deployments from the default branch (`main`),
   and `main` is kept for local runs, so it never deploys.
4. Push to `deploy/web`, or run **Actions → Deploy frontend to GitHub Pages → Run workflow** with
   `deploy/web` selected. The link is `https://<owner>.github.io/<repo>/`.
5. Optional: narrow the sim's CORS to the Pages origin:

   ```bash
   pixi run deploy-space --space <user>/aerozone --code-only --cors-origin https://<owner>.github.io
   ```

## Updating

* **Code:** merge the tested work into `deploy/web` and push. Both workflows run, and the Space rebuilds in a few minutes.
* **Data** (rasters changed after `pixi run data`): run
  `pixi run deploy-space --space <user>/aerozone`. CI cannot do this because the rasters are not in git.
* **Copilot key:** run `pixi run deploy-space --space <user>/aerozone --code-only --set-key`, or edit
  the secret in the Space settings.

## Test the container locally

```bash
docker build -t aerozone .
docker run --rm -p 7860:7860 -e OPENROUTER_API_KEY aerozone
```

Then open <http://127.0.0.1:7860/>.

## Notes

* The demo is public, so anyone with the link can use the Copilot on the OpenRouter key. Set a
  credit limit on the key at <https://openrouter.ai/keys>.
* The sim keeps one shared world. Visitors who open the 3D demo at the same time see and drive
  the same rovers.
