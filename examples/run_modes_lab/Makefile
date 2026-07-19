.PHONY: help up down clean verify logs psql

help:
	@echo "run_modes_lab — sqlery execution modes demonstration"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  help              Show this menu (default)"
	@echo "  up                Build and start all services"
	@echo "  down              Stop all services"
	@echo "  clean             Stop services and remove volumes (DESTRUCTIVE)"
	@echo "  verify            Run verification script (checks all 6 modes)"
	@echo "  logs              Follow service logs"
	@echo "  psql              Open psql shell to the database"
	@echo ""
	@echo "Dashboards:"
	@echo "  Standalone web:   http://localhost:8010"
	@echo "  HTTP trigger:     http://localhost:8001"
	@echo ""
	@echo "Example workflow:"
	@echo "  make up                # Start all services"
	@echo "  make verify            # Check that all modes complete jobs"
	@echo "  make down              # Stop services"

up:
	docker compose build
	docker compose up -d
	@echo ""
	@echo "✓ All services started"
	@echo ""
	@echo "Dashboards:"
	@echo "  Standalone (FastAPI):  http://localhost:8010"
	@echo "  Django server (debug): http://localhost:8001"
	@echo ""
	@read -p "Open standalone dashboard in browser? [y/N] " -t 5 -r REPLY; \
	if [ "$$REPLY" = "y" ] || [ "$$REPLY" = "Y" ]; then \
		if command -v open > /dev/null; then \
			open http://localhost:8010; \
		elif command -v xdg-open > /dev/null; then \
			xdg-open http://localhost:8010; \
		fi; \
	fi

down:
	docker compose down

clean:
	@echo "⚠ Removing all services and volumes (DESTRUCTIVE)"
	docker compose down -v

verify:
	docker compose --profile verify run --rm verifier

logs:
	docker compose logs -f

psql:
	docker compose exec postgres psql -U sqlery -d sqlery_lab
