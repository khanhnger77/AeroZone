# MARS ODYSSEY backend container: planner API (/api), Copilot and sim server (/sim), plus the web
# app itself, all on one public port. Built by the Hugging Face Space (see docs/deployment.md);
# `docker build -t aerozone . && docker run -p 7860:7860 aerozone` runs the same image locally.
#
# The terrain rasters (data/processed, data/analysis) are git-ignored; `pixi run deploy-space`
# uploads them to the Space next to the code. If they are missing the launcher downloads the
# sources (~1.8 GB) and derives them at start-up, which works but takes a long time.
FROM ghcr.io/prefix-dev/pixi:0.67.2-bookworm

# Hugging Face runs the container as uid 1000; build as that user so every file is writable.
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

# 3. The code and data, then the production web build (same-origin: the Space serves /api and /sim).
COPY --chown=user . .
RUN pixi run --locked build

# The static frontend on GitHub Pages calls /sim from another origin; the planner API already
# allows any origin. Set MO_CORS_ORIGINS to the Pages origin in the Space settings to narrow it.
ENV MO_CORS_ORIGINS="*" \
    PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["pixi", "run", "--locked", "demo", "--no-browser", "--no-env-data", "--host", "0.0.0.0", "--port", "7860"]
