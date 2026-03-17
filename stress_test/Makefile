COMPOSE  = TOTAL_JOBS=$(JOBS) docker compose
WORKERS ?= 4
JOBS    ?= 500
NO_CACHE ?=

.PHONY: help sqlery-stress-test rq-stress-test both rebuild clean logs-sqlery logs-rq

help: ## Show this help menu
	@echo "Usage: make <target> [WORKERS=4] [JOBS=500]"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make sqlery-stress-test WORKERS=8 JOBS=1000"
	@echo "  make rq-stress-test JOBS=200"

.DEFAULT_GOAL := help

sqlery-stress-test: ## Run the sqlery stress test
	$(COMPOSE) --profile sqlery up -d --build $(NO_CACHE) \
		--scale sqlery-worker=$(WORKERS) \
		--scale sqlery-producer=0 \
		--scale sqlery-reporter=0
	$(COMPOSE) --profile sqlery run --rm --no-deps sqlery-producer
	$(COMPOSE) --profile sqlery run --rm --no-deps sqlery-reporter
	$(COMPOSE) --profile sqlery down -v

rq-stress-test: ## Run the RQ stress test
	$(COMPOSE) --profile rq up -d --build $(NO_CACHE) \
		--scale rq-worker=$(WORKERS) \
		--scale rq-producer=0 \
		--scale rq-reporter=0
	$(COMPOSE) --profile rq run --rm --no-deps rq-producer
	$(COMPOSE) --profile rq run --rm --no-deps rq-reporter
	$(COMPOSE) --profile rq down -v

both: ## Run both stacks sequentially
	$(MAKE) sqlery-stress-test
	$(MAKE) rq-stress-test

rebuild: ## Force rebuild all images (no cache)
	$(COMPOSE) --profile sqlery --profile rq build --no-cache

logs-sqlery: ## Stream logs for the sqlery stack only
	$(COMPOSE) --profile sqlery logs -f

logs-rq: ## Stream logs for the RQ stack only
	$(COMPOSE) --profile rq logs -f

clean: ## Stop and remove all containers + volumes
	$(COMPOSE) --profile sqlery --profile rq down -v --remove-orphans
