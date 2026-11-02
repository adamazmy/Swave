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

.PHONY: setup db ingest build-index eval api web dev feedback test

WEEKS ?= 8

setup:              ## venv + deps + pre-commit hooks
	$(PYTHON) -m venv api/.venv
	$(VENV_PY) -m pip install -q --upgrade pip
	$(VENV_PY) -m pip install -q -e "api[dev]"
	$(VENV_PY) -m pre_commit install
	npm --prefix web install

db:                 ## start Postgres and wait until healthy
	docker compose up -d --wait db

ingest: db          ## MPD slices -> tracks, playlist_tracks (prune < 5 playlists)
	$(VENV_PY) -m swave.pipeline.ingest

build-index: db     ## top-K neighbours per track -> neighbours table
	$(VENV_PY) -m swave.pipeline.build_index --method $(METHOD) --k $(K) --min-co $(MIN_CO)

eval: db            ## hit-rate@50 / recall@50 on held-out playlists -> eval/results.csv
	$(VENV_PY) -m swave.pipeline.evaluate --method $(METHOD) --k $(K) --min-co $(MIN_CO) --alpha $(ALPHA) $(if $(SCORERS),--scorers $(SCORERS))

api: db             ## FastAPI on http://127.0.0.1:8000 (docs at /docs), auto-reload
	$(VENV_PY) -m uvicorn swave.main:create_app --factory --reload --reload-dir api/swave --host 127.0.0.1 --port 8000

web:                ## Next.js dev server on http://localhost:3000
	npm --prefix web run dev

dev: db             ## API + web together (Ctrl-C stops both)
	npx --prefix web concurrently -k -n api,web -c green,cyan "$(MAKE) api" "npm --prefix web run dev"

feedback: db        ## weekly keep rate + most-removed tracks/artists (terminal only)
	$(VENV_PY) -m swave.pipeline.feedback --weeks $(WEEKS)

test: db            ## API/pipeline tests + web typecheck, lint, unit tests (synthetic data only)
	$(VENV_PY) -m pytest -q api/tests
	npm --prefix web run typecheck
	npm --prefix web run lint
	npm --prefix web test
