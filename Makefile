# Convenience wrapper around the cross-platform task runner (scripts/dev.py).
# Windows users: run `python scripts/dev.py <command>` directly in PowerShell.
PY ?= python3

.PHONY: setup migrate seed run test lint docker-up docker-down

setup:       ; $(PY) scripts/dev.py setup
migrate:     ; $(PY) scripts/dev.py migrate
seed:        ; $(PY) scripts/dev.py seed
run:         ; $(PY) scripts/dev.py run
test:        ; $(PY) scripts/dev.py test
lint:        ; $(PY) scripts/dev.py lint
docker-up:   ; $(PY) scripts/dev.py docker-up
docker-down: ; $(PY) scripts/dev.py docker-down
