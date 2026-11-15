# Online demo: GitHub Pages + Google Cloud Run

A permanent public link that does not depend on anyone's computer. It lives on the `deploy/web`
branch; `main` stays the branch for local runs and never deploys. The work is split between two
hosts:

```
https://<owner>.github.io/<repo>/                GitHub Pages: React app + map data (static files)
        │  HTTPS (fetch) + WSS (live sim state)
        ▼
https://aerozone-<hash>.<region>.run.app        Google Cloud Run: one container, one port
   ├─ /api/*   FastAPI planner: routes, point query, raw images, Copilot
   ├─ /sim/*   aiohttp + PyBullet sim server and its WebSocket
   └─ /        the same web app, so the Cloud Run link alone is also a full demo
```

GitHub Pages can only serve files. The planner, the Copilot and the physics sim need running
Python processes, so they run in a container on Cloud Run. Free hosts with 512 MB of memory are
too small: the container was measured at about 360 MB idle and 1.1 GB with the world demo running.
(Hugging Face, the first choice, now charges for Docker Spaces.)

## Cost

Cloud Run bills only while a request or a sim WebSocket is open (min 0 instances). The monthly
free allowance is 180,000 vCPU-seconds and 360,000 GiB-seconds. With 1 vCPU and 2 GiB that is
about 50 hours of active use a month, ample for a demo. Expect at most a few cents a month for
storing the ~1 GB image beyond the 0.5 GB free Artifact Registry storage. A Google Cloud account
with billing enabled (a card) is required; set a budget alert, for example $1.

When nobody has used it for a while, the first visit starts a container, which takes about
10–20 s. The planner shows "offline" until then.

## How the pieces connect

| Piece | File | What it does |
|---|---|---|
| Backend address | `frontend/src/services/endpoints.ts` (inlined in `api.ts`, `sim.ts`) | `VITE_BACKEND_URL` (build time) prefixes every `/api` and `/sim` call and the WebSockets. Unset means same origin, as in local runs. |
| Sim iframe | `App.tsx`, `sim/web/js/main.js` | The iframe loads from the backend. The planner passes its own origin as `?parent=`, and both sides post messages only to that origin. |
| CORS | `backend/main.py`, `sim/server/sim_server.py` | The planner API allows any origin. The sim allows the origins in `MO_CORS_ORIGINS` (`*` in the image; none when unset, so local runs are unchanged). |
| Container | `Dockerfile`, `.dockerignore` | Pinned pixi environment, `npm ci`, production build, then `pixi run demo --host 0.0.0.0 --port $PORT`. `vite preview` proxies `/api` and `/sim` and accepts `*.run.app` host names. |
| Backend deploy | `scripts/deploy_cloudrun.py` (`pixi run deploy-cloudrun`), `.gcloudignore` | `gcloud run deploy --source .` with the demo settings: 2 GiB, 0–1 instances, 3600 s timeout. `.gcloudignore` keeps the git-ignored rasters in the upload and leaves out `api_key.credentials`. |
| Frontend deploy | `.github/workflows/pages.yml` | On every push to `deploy/web`, builds the frontend with `BACKEND_URL` and publishes it to Pages. |

## One-time setup

### 1. Google Cloud Run (backend)

1. Install the Google Cloud CLI: <https://cloud.google.com/sdk/docs/install>. On Windows you can
   use `winget install Google.CloudSDK`. Open a new terminal afterwards so `gcloud` is on PATH,
   then run `gcloud auth login`.
2. At <https://console.cloud.google.com>, create a project and note its **project ID** (for example
   `aerozone-demo-123`). Under **Billing**, link a billing account to it. Under **Billing →
   Budgets & alerts**, add a small budget.
3. From the repository root on `deploy/web`, with the data built (`pixi run data` / `pixi run data-env`), run:

   ```bash
   pixi run deploy-cloudrun --project <project-id> --set-key
   ```

   The script:
   - enables the Cloud Run, Cloud Build and Artifact Registry APIs;
   - uploads the folder and builds the image in Cloud Build (about 5–10 minutes the first time);
   - deploys the service;
   - with `--set-key`, sets `OPENROUTER_API_KEY` from `api_key.credentials` as an environment
     variable of the service (visible only to the project's owners).

   It ends by printing the service URL.
4. Open `<url>/api/health`. It should show `{"status":"ok",...}`. `<url>/` is the full app.

### 2. GitHub Pages (frontend)

The repository owner does this. Pages is free for public repositories.

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → Variables:** add `BACKEND_URL` = the Cloud Run
   URL from step 1 (no trailing slash).
3. **Settings → Environments → github-pages → Deployment branches and tags → Add rule:**
   `deploy/web`. By default Pages only accepts deployments from the default branch (`main`), and
   `main` is kept for local runs, so it never deploys.
4. Push to `deploy/web`, or run **Actions → Deploy frontend to GitHub Pages → Run workflow** with
   `deploy/web` selected. The link is `https://<owner>.github.io/<repo>/`.
5. Optional: narrow the sim's CORS to the Pages origin:

   ```bash
   pixi run deploy-cloudrun --project <project-id> --cors-origin https://<owner>.github.io
   ```

## Updating

* **Frontend:** merge the tested work into `deploy/web` and push. Pages rebuilds in about 2 minutes.
* **Backend** (code or data changed): run `pixi run deploy-cloudrun --project <project-id>` from
  `deploy/web`. The key and CORS settings are kept unless you pass them again. The backend is not
  deployed from CI because the rasters are not in git.

## Test the container locally

```bash
docker build -t aerozone .
docker run --rm -p 7860:7860 -e OPENROUTER_API_KEY aerozone
```

Then open <http://127.0.0.1:7860/>.

## Notes

* The demo is public, so anyone with the link can use the Copilot on the OpenRouter key. Set a
  credit limit on the key at <https://openrouter.ai/keys>.
* The sim keeps one shared world, so the service runs at most one instance. Visitors who open the
  3D demo at the same time see and drive the same rovers.
* Cloud Run closes a WebSocket after the 3600 s request timeout; the app reconnects.
