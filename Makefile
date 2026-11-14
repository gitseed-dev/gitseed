VERSION ?= dev
# Local builds bundle proprietary agent CLIs (Claude Code, Antigravity, Grok)
# for convenience. Use `make build INCLUDE_PROPRIETARY_CLIS=false` for any
# runner image you push to a registry or otherwise redistribute.
INCLUDE_PROPRIETARY_CLIS ?= true

.DEFAULT_GOAL := help
.PHONY: help init setup dev test lint build migrate

help:     ## list available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init:     ## generate .env with strong random secrets (on Windows PowerShell: .\scripts\init.ps1)
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/init.ps1
else
	./scripts/init.sh
endif

setup:    ## install all dependencies
	uv sync --all-packages
	pnpm install

dev:      ## build and run the full stack in Docker Compose (no hot reload; re-run after code changes)
	docker compose -f compose.yaml up --build

test:     ## run all tests
	uv run python -m pytest
	pnpm --filter web test

lint:     ## lint and type-check
	uv run ruff check .
	uv run mypy packages/common apps/core apps/cli apps/mcp apps/runner
	pnpm --filter web lint

build:    ## build both images (local runner image includes proprietary CLIs; INCLUDE_PROPRIETARY_CLIS=false for images you redistribute)
	docker build --build-arg GITSEED_VERSION=$(VERSION) -t gitseed/gitseed:$(VERSION) .
	docker build \
		--build-arg GITSEED_VERSION=$(VERSION) \
		--build-arg INCLUDE_PROPRIETARY_CLIS=$(INCLUDE_PROPRIETARY_CLIS) \
		--build-arg INSTALL_CLAUDE_CODE=$${INSTALL_CLAUDE_CODE:-$(INCLUDE_PROPRIETARY_CLIS)} \
		--build-arg INSTALL_ANTIGRAVITY=$${INSTALL_ANTIGRAVITY:-$(INCLUDE_PROPRIETARY_CLIS)} \
		--build-arg INSTALL_GROK_CLI=$${INSTALL_GROK_CLI:-$(INCLUDE_PROPRIETARY_CLIS)} \
		-t gitseed/runner:$(VERSION) apps/runner

migrate:  ## apply database migrations
	docker compose run --rm api migrate