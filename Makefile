VERSION ?= dev

.DEFAULT_GOAL := help
.PHONY: help init setup dev test lint build migrate

help:     ## list available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init:     ## generate .env with strong random secrets
	./scripts/init.sh

setup:    ## install all dependencies
	uv sync --all-packages
	pnpm install

dev:      ## run the dev stack with hot reload
	docker compose -f compose.yaml up --build

test:     ## run all tests
	uv run python -m pytest
	pnpm --filter web test

lint:     ## lint and type-check
	uv run ruff check .
	uv run mypy packages/common apps/core apps/cli apps/mcp apps/runner
	pnpm --filter web lint

build:    ## build both images
	docker build --build-arg GITSEED_VERSION=$(VERSION) -t gitseed/gitseed:$(VERSION) .
	docker build \
		--build-arg GITSEED_VERSION=$(VERSION) \
		--build-arg INCLUDE_PROPRIETARY_CLIS=$${INCLUDE_PROPRIETARY_CLIS:-true} \
		--build-arg INSTALL_CLAUDE_CODE=$${INSTALL_CLAUDE_CODE:-$${INCLUDE_PROPRIETARY_CLIS:-true}} \
		--build-arg INSTALL_ANTIGRAVITY=$${INSTALL_ANTIGRAVITY:-$${INCLUDE_PROPRIETARY_CLIS:-true}} \
		--build-arg INSTALL_GROK_CLI=$${INSTALL_GROK_CLI:-$${INCLUDE_PROPRIETARY_CLIS:-true}} \
		-t gitseed/runner:$(VERSION) apps/runner

migrate:  ## apply database migrations
	docker compose run --rm api migrate