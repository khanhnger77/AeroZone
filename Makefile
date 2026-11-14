# MARS ODYSSEY — common tasks. Requires the `mars` conda env (see environment.yml).
CONDA_ENV ?= $(HOME)/miniconda3/envs/mars
PY  := $(CONDA_ENV)/bin/python
export PATH := $(CONDA_ENV)/bin:$(PATH)

.PHONY: env data backend frontend test

env:
	conda env create -f environment.yml || conda env update -f environment.yml
	cd frontend && npm install

data:            ## download + validate + process all layers (~3 min after the 1.8 GB download)
	$(PY) scripts/download_data.py
	$(PY) scripts/validate_data.py
	$(PY) scripts/process_terrain.py
	$(PY) scripts/generate_hazard.py
	$(PY) scripts/render_layers.py
	$(PY) scripts/build_science_targets.py

backend:
	$(CONDA_ENV)/bin/uvicorn backend.main:app --port 8000 --reload --reload-dir backend

frontend:
	cd frontend && npx vite --port 5173

test:
	env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PY) -m pytest -q tests
