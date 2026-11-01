# Swave: run from the repo root. Windows: `winget install ezwinports.make`.
ifeq ($(OS),Windows_NT)
  PYTHON ?= py -3.12
  VENV_PY := api/.venv/Scripts/python.exe
else
  PYTHON ?= python3.12
  VENV_PY := api/.venv/bin/python
endif

METHOD ?= cosine
K ?= 200
MIN_CO ?= 2
ALPHA ?= 0

.PHONY: setup db ingest build-index eval api test

setup:              ## venv + deps + pre-commit hooks
	$(PYTHON) -m venv api/.venv
	$(VENV_PY) -m pip install -q --upgrade pip
	$(VENV_PY) -m pip install -q -e "api[dev]"
	$(VENV_PY) -m pre_commit install

db:                 ## start Postgres and wait until healthy
	docker compose up -d --wait db

ingest: db          ## MPD slices -> tracks, playlist_tracks (prune < 5 playlists)
	$(VENV_PY) -m swave.pipeline.ingest

build-index: db     ## top-K neighbours per track -> neighbours table
	$(VENV_PY) -m swave.pipeline.build_index --method $(METHOD) --k $(K) --min-co $(MIN_CO)

eval: db            ## hit-rate@50 / recall@50 on held-out playlists -> eval/results.csv
	$(VENV_PY) -m swave.pipeline.evaluate --method $(METHOD) --k $(K) --min-co $(MIN_CO) --alpha $(ALPHA)

api: db             ## FastAPI on http://127.0.0.1:8000 (docs at /docs), auto-reload
	$(VENV_PY) -m uvicorn swave.main:create_app --factory --reload --reload-dir api/swave --host 127.0.0.1 --port 8000

test: db            ## unit + API tests (synthetic fixtures only; API tests use a throwaway swave_test DB)
	$(VENV_PY) -m pytest -q api/tests
