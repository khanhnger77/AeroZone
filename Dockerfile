# MARS ODYSSEY backend container: planner API (/api), Copilot and sim server (/sim), plus the web
# app itself, all on one public port. Deployed to Google Cloud Run by `pixi run deploy-cloudrun`
# (see docs/deployment.md); `docker build -t aerozone . && docker run -p 7860:7860 aerozone`
# runs the same image locally.
#
# The terrain rasters (data/processed, data/analysis) are git-ignored but included in the build
# context (.dockerignore and .gcloudignore keep them), so they come from the machine that deploys.
# If they are missing the launcher downloads the sources (~1.8 GB) and derives them at start-up,
# which works but takes a long time.
FROM ghcr.io/prefix-dev/pixi:0.67.2-bookworm

# Run as an ordinary user that owns every file it may need to write.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user
WORKDIR /home/user/app

# 1. The exact environment from pixi.lock (cached until the manifest or lock file changes).
COPY --chown=user pixi.toml pixi.lock ./
RUN pixi install --locked

# 2. Frontend dependencies (cached until package-lock.json changes).
COPY --chown=user frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && pixi run --locked npm ci --no-audit --no-fund

# 3. The code and data, then the production web build (same origin: the container serves /api and /sim).
COPY --chown=user . .
RUN pixi run --locked build

# The static frontend on GitHub Pages calls /sim from another origin; the planner API already
# allows any origin. `deploy-cloudrun --cors-origin` narrows MO_CORS_ORIGINS to the Pages origin.
ENV MO_CORS_ORIGINS="*" \
    PYTHONUNBUFFERED=1 \
    PORT=7860
EXPOSE 7860
# Cloud Run sets PORT; the web app listens there and proxies /api (:8000) and /sim (:8100).
CMD ["sh", "-c", "exec pixi run --locked demo --no-browser --no-env-data --host 0.0.0.0 --port \"$PORT\""]
