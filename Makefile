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

PRIVATE = docker compose -f docker-compose.yml -f docker-compose.private.yml --env-file .env.private

.PHONY: private-up private-down private-logs private-update backup setup db ingest build-index lb-ingest lb-names lb-index lb-install artist-index discogs-styles lb-build lb-report eval api web dev feedback test preview-report preview-bench

WEEKS ?= 8
DAYS ?= 7
SAMPLE ?= 300
LB_SINCE ?= 2023-11-01
HOLDOUT ?= mpd
LB_MIN_LISTENERS ?= 20
LB_K ?= 100

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

lb-ingest:          ## ListenBrainz listens since LB_SINCE -> data/lb/ (range-reads the remote dump)
	$(VENV_PY) -m swave.pipeline.lb_ingest --since $(LB_SINCE)

lb-names:           ## fold data/lb/names/<file>.parquet into one deduplicated table (verified)
	$(VENV_PY) -m swave.pipeline.lb_names --delete-inputs

lb-index: db        ## candidate LB index -> data/lb/runs/$(RUN)/ (app tables untouched); ARGS="--min-listeners 10 ..."
	$(VENV_PY) -m swave.pipeline.lb_index --run $(RUN) $(ARGS)

lb-eval: db         ## time-split eval of data/lb/runs/$(RUN) (LB only + the app's mix) -> eval/results.csv
	$(VENV_PY) -m swave.pipeline.evaluate --holdout time --lb-run $(RUN) $(if $(LB_K),--lb-k $(LB_K)) --scorers eval/scorers/lb.json scorers.json

lb-install: db      ## data/lb/runs/$(RUN) -> app tables (old ones kept as *_old); ARGS="--rollback" / "--drop-old"
	$(VENV_PY) -m swave.pipeline.lb_install $(if $(RUN),--run $(RUN)) $(ARGS)

artist-index: db    ## top songs per artist (tracks + lb_tracks) for the related-artists scorer (~30 s)
	$(VENV_PY) -m swave.pipeline.artist_index

discogs-styles: db  ## artist_styles from a Discogs masters dump (CC0): DUMP=data/discogs/discogs_YYYYMMDD_masters.xml.gz
	$(VENV_PY) -m swave.pipeline.discogs_styles $(DUMP)

lb-build: db        ## data/lb/ -> sessions, matching, lb_tracks, neighbours_lb, lb_holdout
	$(VENV_PY) -m swave.pipeline.lb_build --min-listeners $(LB_MIN_LISTENERS) --k $(LB_K)

lb-report: db       ## LB match rate + post-2017 share of new tracks (MusicBrainz API sample, ~17 min)
	$(VENV_PY) -m swave.pipeline.lb_report

eval: db            ## hit-rate@50 / recall@50 on held-out playlists -> eval/results.csv
	$(VENV_PY) -m swave.pipeline.evaluate --method $(METHOD) --k $(K) --min-co $(MIN_CO) --alpha $(ALPHA) $(if $(SCORERS),--scorers $(SCORERS)) --holdout $(HOLDOUT)

api: db             ## FastAPI on http://127.0.0.1:8000 (docs at /docs), auto-reload
	$(VENV_PY) -m uvicorn swave.main:create_app --factory --reload --reload-dir api/swave --host 127.0.0.1 --port 8000

web:                ## Next.js dev server on http://localhost:3000
	npm --prefix web run dev

dev: db             ## API + web together (Ctrl-C stops both)
	npx --prefix web concurrently -k -n api,web -c green,cyan "$(MAKE) api" "npm --prefix web run dev"

feedback: db        ## weekly keep rate + most-removed tracks/artists (terminal only)
	$(VENV_PY) -m swave.pipeline.feedback --weeks $(WEEKS)

preview-report: db  ## preview coverage % + outcome breakdown, last DAYS days (terminal only)
	$(VENV_PY) -m swave.pipeline.preview_report --days $(DAYS)

preview-bench: db   ## old vs new preview resolver coverage on SAMPLE shown tracks (live APIs)
	$(VENV_PY) -m swave.pipeline.preview_bench --sample $(SAMPLE)

test: db            ## API/pipeline tests + web typecheck, lint, unit tests (synthetic data only)
	$(VENV_PY) -m pytest -q api/tests
	npm --prefix web run typecheck
	npm --prefix web run lint
	npm --prefix web test

# ── Private deployment (docs/PRIVATE.md) ──
private-up:         ## start Swave for friends: production api + web + Cloudflare tunnel
	$(PRIVATE) up -d --build

private-down:       ## stop the private instance (the database keeps running)
	$(PRIVATE) stop api web cloudflared

private-logs:       ## follow the private instance's logs
	$(PRIVATE) logs -f --tail 100 api web cloudflared

private-update:     ## deploy the current checkout: rebuild + restart api and web
	$(PRIVATE) up -d --build api web

backup:             ## back up user data now (scripts/backup.ps1, to SWAVE_BACKUP_DIR)
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup.ps1
