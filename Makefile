# Linux/macOS alternative to pixi, using the conda env from environment.yml.
# pixi (pixi.toml) is the reference; these targets mirror its tasks.
SHELL := /bin/bash
ENV ?= mars
RUN := conda run --no-capture-output -n $(ENV)

.PHONY: env data data-env backend frontend sim demo test

env:
	conda env create -f environment.yml || conda env update -f environment.yml

data:
	$(RUN) python scripts/download_data.py
	$(RUN) python scripts/validate_data.py
	$(RUN) python scripts/process_terrain.py
	$(RUN) python scripts/generate_hazard.py
	$(RUN) python scripts/render_layers.py
	$(RUN) python scripts/build_science_targets.py

data-env:
	$(RUN) python scripts/download_env_data.py
	$(RUN) python scripts/process_geology.py
	$(RUN) python scripts/generate_hazard.py
	$(RUN) python scripts/render_layers.py
	$(RUN) python scripts/build_climate.py
	$(RUN) python scripts/fetch_space_weather.py

backend:
	$(RUN) uvicorn backend.main:app --port 8000 --reload --reload-dir backend

frontend:
	cd frontend && $(RUN) npm install && $(RUN) npx vite --port 5173

sim:
	$(RUN) python -m sim.server.sim_server

demo:
	$(RUN) python -m sim.tools.run_demo

test:
	$(RUN) python -m pytest -q sim/tests sim/obstacle_avoid/tests tests scripts/tests
