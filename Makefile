# Sqlery Makefile - Comprehensive Local Development & Testing
# =============================================================
# This Makefile provides targets for running Sqlery in various configurations
# including single worker, multiple workers, separate queues, and more.

# Colors for output
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[1;33m
BLUE := \033[0;34m
CYAN := \033[0;36m
NC := \033[0m # No Color

# Python configuration
PYTHON := python3
VENV := .venv
VENV_BIN := $(VENV)/bin
PYTHON_VENV := $(VENV_BIN)/python
PIP_VENV := $(VENV_BIN)/pip
DJANGO_MANAGE := uv run sample_project/manage.py
UV := uv

# Project paths
SAMPLE_PROJECT := sample_project
CONFIG_DIR := .makefile-configs
LOG_DIR := .makefile-logs
PID_DIR := .makefile-pids

# Configuration files
CURRENT_CONFIG := $(CONFIG_DIR)/current.env

# =============================================================================
# Reusable Functions (GNU Make 'call' syntax)
# =============================================================================

# Usage: $(call require-param,PARAM_NAME,param-value,usage-example)
# Validates that a required parameter is provided
define require-param
	@if [ -z "$(2)" ]; then \
		echo "$(RED)✗ Error: $(1) not specified$(NC)"; \
		echo "  Usage: $(3)"; \
		exit 1; \
	fi
endef

# Usage: $(call print-banner,Title Text)
# Prints a cyan banner with title (40 char width)
define print-banner
	@echo "$(CYAN)════════════════════════════════════════$(NC)"
	@echo "$(GREEN)$(1)$(NC)"
	@echo "$(CYAN)════════════════════════════════════════$(NC)"
	@echo ""
endef

# Usage: $(call print-header,Title Text)
# Prints a large header for help/menu sections (67 char width)
define print-header
	@echo "$(CYAN)═══════════════════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)                    $(1)                         $(NC)"
	@echo "$(CYAN)═══════════════════════════════════════════════════════════════════$(NC)"
	@echo ""
endef

# Usage: $(call warning-prompt,Warning message)
# Prints warning and waits 3 seconds for cancellation
define warning-prompt
	@echo "$(RED)⚠️  $(1). Press Ctrl+C to cancel...$(NC)"
	@sleep 3
endef

# Usage: $(call start-worker,worker-name,log-file,pid-file,env-vars)
# Starts a background worker with logging and PID tracking
define start-worker
	@echo "  $(CYAN)Starting $(1)...$(NC)"
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	@$(4) $(DJANGO_MANAGE) run_jobs --verbosity=2 > $(2) 2>&1 & echo $$! > $(3)
endef

# Usage: $(call clear-queued-jobs)
# Clears all queued jobs from database
define clear-queued-jobs
	@$(DJANGO_MANAGE) shell -c "from sqlery.models import QueuedJob; QueuedJob.objects.filter(status='queued').delete()"
endef

# Usage: $(call print-help-section,Section Name,grep-pattern)
# Prints a help section with targets matching pattern
define print-help-section
	@echo "$(YELLOW)$(1):$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -E "$(2)" | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-25s$(NC) %s\n", $$1, $$2}'
	@echo ""
endef

# =============================================================================
# Default Target & Interactive Menu
# =============================================================================

.DEFAULT_GOAL := menu

menu: ## Interactive menu (default when running 'make')
	$(call print-header,Sqlery - Interactive Menu)
	@PS3="$(BLUE)Select an option (1-14): $(NC)"; \
	options=( \
		"Setup (first-time installation)" \
		"Start single worker (foreground)" \
		"Start multiple workers (background)" \
		"Stop all workers" \
		"View worker status" \
		"View jobs status" \
		"Populate database with sample jobs" \
		"View jobs list" \
		"Enqueue demo jobs" \
		"View logs" \
		"Configuration management" \
		"Show all available commands (help)" \
		"Clean up" \
		"Exit" \
	); \
	select opt in "$${options[@]}"; do \
		case $$REPLY in \
			1) echo "$(BLUE)→ Running setup...$(NC)"; $(MAKE) setup; break;; \
			2) echo "$(BLUE)→ Starting single worker (press Ctrl+C to stop)...$(NC)"; $(MAKE) worker; break;; \
			3) echo "$(BLUE)→ How many workers?$(NC)"; \
			   read -p "Number of workers (default 4): " num; \
			   num=$${num:-4}; \
			   echo "$(BLUE)→ Starting $$num workers in background...$(NC)"; \
			   $(MAKE) workers-parallel NUM=$$num; \
			   break;; \
			4) echo "$(BLUE)→ Stopping all workers...$(NC)"; $(MAKE) workers-stop; break;; \
			5) echo "$(BLUE)→ Checking worker status...$(NC)"; $(MAKE) workers-status; break;; \
			6) echo "$(BLUE)→ Viewing jobs status...$(NC)"; $(MAKE) jobs-status; break;; \
			7) echo "$(BLUE)→ Select database population option:$(NC)"; \
			   echo "  1) Sample dataset (~30 jobs with all features)"; \
			   echo "  2) Large dataset (120+ jobs for load testing)"; \
			   echo "  3) Various states (queued, running, success, failed)"; \
			   read -p "Choice (1-3): " db_opt; \
			   case $$db_opt in \
			   	   1) $(MAKE) populate-db;; \
			   	   2) $(MAKE) populate-db-large;; \
			   	   3) $(MAKE) populate-db-states;; \
			   	   *) echo "$(RED)Invalid choice$(NC)";; \
			   esac; \
			   break;; \
			8) echo "$(BLUE)→ Viewing jobs list...$(NC)"; $(MAKE) jobs-list; break;; \
			9) echo "$(BLUE)→ Enqueueing demo jobs...$(NC)"; $(MAKE) demo-jobs; break;; \
			10) echo "$(BLUE)→ Viewing logs (press Ctrl+C to stop)...$(NC)"; $(MAKE) logs; break;; \
			11) echo "$(BLUE)→ Configuration Management:$(NC)"; \
			    echo "  1) List available configurations"; \
			    echo "  2) Switch configuration"; \
			    echo "  3) Show current configuration"; \
			    read -p "Choice (1-3): " cfg_opt; \
			    case $$cfg_opt in \
			        1) $(MAKE) config-list;; \
			        2) $(MAKE) config-list; \
			           read -p "Enter config name: " cfg_name; \
			           $(MAKE) config-use CONFIG=$$cfg_name;; \
			        3) $(MAKE) config-show;; \
			        *) echo "$(RED)Invalid choice$(NC)";; \
			    esac; \
			    break;; \
			12) $(MAKE) help; break;; \
			13) echo "$(BLUE)→ Cleaning up...$(NC)"; $(MAKE) clean; break;; \
			14) echo "$(GREEN)Goodbye!$(NC)"; exit 0;; \
			*) echo "$(RED)Invalid option. Please select 1-14.$(NC)";; \
		esac; \
	done

.PHONY: help
help: ## Show this help message
	$(call print-header,Sqlery Makefile - Help)
	$(call print-help-section,Setup & Installation,setup|install|init)
	$(call print-help-section,Configuration,config)
	$(call print-help-section,Database,db|migrate)
	$(call print-help-section,Single Worker,worker:)
	$(call print-help-section,Multiple Workers,workers)
	$(call print-help-section,Testing & Development,test|dev|demo|jobs)
	$(call print-help-section,Docker,docker)
	$(call print-help-section,Cleanup & Utilities,clean|stop|logs|status)

# =============================================================================
# Setup & Installation
# =============================================================================

.PHONY: setup
setup: install init-config init-db ## Complete setup (venv, config, database)
	@echo "$(GREEN)✓ Setup complete! Run 'make help' to see available commands$(NC)"

.PHONY: install
install: ## Install Python dependencies in virtual environment
	@echo "$(BLUE)→ Creating virtual environment and installing dependencies with uv...$(NC)"
	@$(UV) sync
	@$(UV) pip install -r $(SAMPLE_PROJECT)/requirements.txt
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

.PHONY: init-config
init-config: ## Initialize configuration directory with examples
	@echo "$(BLUE)→ Creating configuration directory...$(NC)"
	@mkdir -p $(CONFIG_DIR) $(LOG_DIR) $(PID_DIR)
	@$(MAKE) -s create-config-examples
	@echo "$(GREEN)✓ Configuration initialized$(NC)"
	@echo "$(YELLOW)  Available configs:$(NC)"
	@ls -1 $(CONFIG_DIR)/*.env.example 2>/dev/null | xargs -n1 basename || true

.PHONY: create-config-examples
create-config-examples:
	@echo "# Default Configuration" > $(CONFIG_DIR)/default.env.example
	@echo "SQLERY_TRIGGER_MODE=middleware" >> $(CONFIG_DIR)/default.env.example
	@echo "SQLERY_MAX_WORKERS=1" >> $(CONFIG_DIR)/default.env.example
	@echo "SQLERY_WORKER_QUEUES=default" >> $(CONFIG_DIR)/default.env.example
	@echo "" >> $(CONFIG_DIR)/default.env.example
	@echo "# Multi-Worker Configuration" > $(CONFIG_DIR)/multi-worker.env.example
	@echo "SQLERY_TRIGGER_MODE=daemon" >> $(CONFIG_DIR)/multi-worker.env.example
	@echo "SQLERY_MAX_WORKERS=4" >> $(CONFIG_DIR)/multi-worker.env.example
	@echo "SQLERY_WORKER_QUEUES=high,default,low" >> $(CONFIG_DIR)/multi-worker.env.example
	@echo "" >> $(CONFIG_DIR)/multi-worker.env.example
	@echo "# High Priority Queue Only" > $(CONFIG_DIR)/queue-high.env.example
	@echo "SQLERY_WORKER_QUEUES=high" >> $(CONFIG_DIR)/queue-high.env.example
	@echo "" >> $(CONFIG_DIR)/queue-high.env.example
	@echo "# Low Priority Queue Only" > $(CONFIG_DIR)/queue-low.env.example
	@echo "SQLERY_WORKER_QUEUES=low" >> $(CONFIG_DIR)/queue-low.env.example
	@echo "" >> $(CONFIG_DIR)/queue-low.env.example
	@echo "# EventBridge Mode" > $(CONFIG_DIR)/eventbridge.env.example
	@echo "SQLERY_TRIGGER_MODE=eventbridge" >> $(CONFIG_DIR)/eventbridge.env.example
	@echo "SQLERY_EVENTBRIDGE_LAMBDA_ARN=arn:aws:lambda:us-east-1:123456789012:function:sqlery-worker" >> $(CONFIG_DIR)/eventbridge.env.example
	@echo "" >> $(CONFIG_DIR)/eventbridge.env.example
	@echo "# HTTP Trigger Mode" > $(CONFIG_DIR)/http-trigger.env.example
	@echo "SQLERY_TRIGGER_MODE=http" >> $(CONFIG_DIR)/http-trigger.env.example
	@echo "SQLERY_INTERNAL_SECRET=change-me-in-production" >> $(CONFIG_DIR)/http-trigger.env.example
	@echo "SQLERY_INTERNAL_BASE_URL=http://localhost:9100" >> $(CONFIG_DIR)/http-trigger.env.example

.PHONY: init-db
init-db: ## Initialize database and run migrations
	@echo "$(BLUE)→ Running migrations...$(NC)"
	@$(DJANGO_MANAGE) migrate
	@echo "$(GREEN)✓ Database initialized$(NC)"

.PHONY: createsuperuser
createsuperuser: ## Create Django admin superuser (interactive)
	@echo "$(BLUE)→ Creating superuser...$(NC)"
	@$(DJANGO_MANAGE) createsuperuser

.PHONY: createsuperuser-auto
createsuperuser-auto: ## Create default superuser (admin/admin) non-interactively
	@echo "$(BLUE)→ Creating default superuser (admin/admin)...$(NC)"
	@$(DJANGO_MANAGE) shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); exists = User.objects.filter(username='admin').exists(); User.objects.create_superuser('admin', 'admin@example.com', 'admin') if not exists else None; print('✓ Superuser created: username=admin, password=admin') if not exists else print('⚠  Superuser admin already exists')"

# =============================================================================
# Configuration Management
# =============================================================================

.PHONY: config-list
config-list: ## List available configurations
	@echo "$(CYAN)Available configurations:$(NC)"
	@ls -1 $(CONFIG_DIR)/*.env.example 2>/dev/null | xargs -n1 basename | sed 's/\.env\.example$$//' || echo "  (none - run 'make init-config')"

.PHONY: config-use
config-use: ## Use a specific config (e.g., make config-use CONFIG=multi-worker)
	$(call require-param,CONFIG,$(CONFIG),make config-use CONFIG=<name>)
	@if [ ! -f "$(CONFIG_DIR)/$(CONFIG).env.example" ]; then \
		echo "$(RED)✗ Error: Configuration '$(CONFIG)' not found$(NC)"; \
		echo "  Available configs:"; \
		$(MAKE) -s config-list; \
		exit 1; \
	fi
	@cp $(CONFIG_DIR)/$(CONFIG).env.example $(CURRENT_CONFIG)
	@echo "$(GREEN)✓ Using configuration: $(CONFIG)$(NC)"
	@echo "$(YELLOW)  Settings:$(NC)"
	@cat $(CURRENT_CONFIG) | grep -v "^#" | grep -v "^$$"

.PHONY: config-show
config-show: ## Show current configuration
	@if [ -f "$(CURRENT_CONFIG)" ]; then \
		echo "$(CYAN)Current configuration:$(NC)"; \
		cat $(CURRENT_CONFIG) | grep -v "^#" | grep -v "^$$"; \
	else \
		echo "$(YELLOW)No configuration selected. Using defaults.$(NC)"; \
	fi

.PHONY: config-edit
config-edit: ## Edit a configuration (e.g., make config-edit CONFIG=default)
	$(call require-param,CONFIG,$(CONFIG),make config-edit CONFIG=<name>)
	@$$EDITOR $(CONFIG_DIR)/$(CONFIG).env.example

# =============================================================================
# Database Management
# =============================================================================

.PHONY: db-migrate
db-migrate: ## Run database migrations
	@$(DJANGO_MANAGE) migrate

.PHONY: db-reset
db-reset: ## Reset database (WARNING: deletes all data)
	$(call warning-prompt,This will delete ALL data)
	@rm -f $(SAMPLE_PROJECT)/db.sqlite3
	@$(MAKE) init-db
	@echo "$(GREEN)✓ Database reset complete$(NC)"

.PHONY: db-shell
db-shell: ## Open Django database shell
	@$(DJANGO_MANAGE) dbshell

# =============================================================================
# Single Worker
# =============================================================================

.PHONY: worker
worker: ## Start single worker (default queue)
	@echo "$(BLUE)→ Starting single worker on 'default' queue...$(NC)"
	@$(DJANGO_MANAGE) run_jobs --verbosity=2

.PHONY: worker-once
worker-once: ## Process jobs once and exit
	@echo "$(BLUE)→ Processing jobs once...$(NC)"
	@$(DJANGO_MANAGE) run_jobs --once --verbosity=2

.PHONY: worker-queue
worker-queue: ## Start worker on specific queue (e.g., make worker-queue QUEUE=high)
	$(call require-param,QUEUE,$(QUEUE),make worker-queue QUEUE=<queue-name>)
	@echo "$(BLUE)→ Starting worker on '$(QUEUE)' queue...$(NC)"
	@SQLERY_WORKER_QUEUES=$(QUEUE) $(DJANGO_MANAGE) run_jobs --verbosity=2

.PHONY: worker-max-jobs
worker-max-jobs: ## Worker with job limit (e.g., make worker-max-jobs MAX=10)
	$(call require-param,MAX,$(MAX),make worker-max-jobs MAX=<number>)
	@echo "$(BLUE)→ Starting worker (max $(MAX) jobs)...$(NC)"
	@$(DJANGO_MANAGE) run_jobs --max-jobs=$(MAX) --verbosity=2

# =============================================================================
# Multiple Workers (Parallel)
# =============================================================================

.PHONY: workers-parallel
workers-parallel: ## Start multiple workers in parallel (default 4)
	@echo "$(BLUE)→ Starting $(or $(NUM),4) workers in parallel...$(NC)"
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	@for i in $$(seq 1 $(or $(NUM),4)); do \
		echo "  $(CYAN)Starting worker $$i...$(NC)"; \
		$(DJANGO_MANAGE) run_jobs --verbosity=2 > $(LOG_DIR)/worker-$$i.log 2>&1 & \
		echo $$! > $(PID_DIR)/worker-$$i.pid; \
	done
	@echo "$(GREEN)✓ Started $(or $(NUM),4) workers$(NC)"
	@echo "$(YELLOW)  Check logs: make logs$(NC)"
	@echo "$(YELLOW)  Stop workers: make workers-stop$(NC)"

.PHONY: workers-separate-queues
workers-separate-queues: ## Start separate workers for each queue (high, default, low)
	@echo "$(BLUE)→ Starting separate workers for each queue...$(NC)"
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	$(call start-worker,HIGH priority worker,$(LOG_DIR)/worker-high.log,$(PID_DIR)/worker-high.pid,SQLERY_WORKER_QUEUES=high)
	$(call start-worker,DEFAULT priority worker,$(LOG_DIR)/worker-default.log,$(PID_DIR)/worker-default.pid,SQLERY_WORKER_QUEUES=default)
	$(call start-worker,LOW priority worker,$(LOG_DIR)/worker-low.log,$(PID_DIR)/worker-low.pid,SQLERY_WORKER_QUEUES=low)
	@echo "$(GREEN)✓ Started 3 queue-specific workers$(NC)"
	@echo "$(YELLOW)  Check logs: make logs-worker-high, make logs-worker-default, make logs-worker-low$(NC)"
	@echo "$(YELLOW)  Stop workers: make workers-stop$(NC)"

.PHONY: workers-multi-queue
workers-multi-queue: ## Start 2 workers per queue (6 total)
	@echo "$(BLUE)→ Starting 2 workers per queue (6 total)...$(NC)"
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	@for q in high default low; do \
		for i in 1 2; do \
			echo "  $(CYAN)Starting $$q queue worker $$i...$(NC)"; \
			SQLERY_WORKER_QUEUES=$$q $(DJANGO_MANAGE) run_jobs --verbosity=2 > $(LOG_DIR)/worker-$$q-$$i.log 2>&1 & \
			echo $$! > $(PID_DIR)/worker-$$q-$$i.pid; \
		done; \
	done
	@echo "$(GREEN)✓ Started 6 workers (2 per queue)$(NC)"
	@echo "$(YELLOW)  Stop workers: make workers-stop$(NC)"

.PHONY: workers-stop
workers-stop: ## Stop all background workers
	@echo "$(BLUE)→ Stopping all workers...$(NC)"
	@if [ -d "$(PID_DIR)" ]; then \
		for pidfile in $(PID_DIR)/worker-*.pid; do \
			if [ -f "$$pidfile" ]; then \
				pid=$$(cat "$$pidfile"); \
				if kill -0 $$pid 2>/dev/null; then \
					echo "  $(YELLOW)Stopping worker (PID $$pid)...$(NC)"; \
					kill $$pid 2>/dev/null || true; \
					sleep 0.5; \
					kill -9 $$pid 2>/dev/null || true; \
				fi; \
				rm "$$pidfile"; \
			fi; \
		done; \
		echo "$(GREEN)✓ All workers stopped$(NC)"; \
	else \
		echo "$(YELLOW)No workers running$(NC)"; \
	fi

.PHONY: workers-status
workers-status: ## Show status of all workers
	@echo "$(CYAN)Worker Status:$(NC)"
	@if [ -d "$(PID_DIR)" ] && [ -n "$$(ls $(PID_DIR)/worker-*.pid 2>/dev/null)" ]; then \
		for pidfile in $(PID_DIR)/worker-*.pid; do \
			if [ -f "$$pidfile" ]; then \
				pid=$$(cat "$$pidfile"); \
				name=$$(basename "$$pidfile" .pid); \
				if kill -0 $$pid 2>/dev/null; then \
					echo "  $(GREEN)✓$(NC) $$name (PID $$pid) - running"; \
				else \
					echo "  $(RED)✗$(NC) $$name (PID $$pid) - not running"; \
				fi; \
			fi; \
		done; \
	else \
		echo "  $(YELLOW)No workers running$(NC)"; \
	fi

# =============================================================================
# Testing & Development
# =============================================================================

.PHONY: dev
dev: ## Start Django development server
	@echo "$(BLUE)→ Starting Django development server...$(NC)"
	@$(DJANGO_MANAGE) runserver 9100

.PHONY: dev-remote
dev-remote: ## Start Django development server accessible from network (0.0.0.0:9100)
	@echo "$(BLUE)→ Starting Django development server on all interfaces...$(NC)"
	@echo "$(YELLOW)⚠  WARNING: Only use this for development/testing!$(NC)"
	@DJANGO_ALLOW_REMOTE=1 $(DJANGO_MANAGE) runserver 0.0.0.0:9100

.PHONY: shell
shell: ## Open Django shell
	@$(DJANGO_MANAGE) shell

.PHONY: test
test: ## Run tests
	@echo "$(BLUE)→ Running tests...$(NC)"
	@$(PYTHON_VENV) -m pytest tests/ -v

.PHONY: demo-jobs
demo-jobs: ## Enqueue demo jobs for testing
	@echo "$(BLUE)→ Enqueueing demo jobs...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import slow_task, fast_task, failing_task; \
		print('Enqueueing 5 fast jobs (default queue)...'); \
		for i in range(5): \
			fast_task.enqueue(number=i, queue='default', priority=0); \
		print('Enqueueing 3 slow jobs (default queue)...'); \
		for i in range(3): \
			slow_task.enqueue(seconds=5, number=i, queue='default', priority=0); \
		print('Enqueueing 2 high-priority jobs...'); \
		for i in range(2): \
			fast_task.enqueue(number=100+i, queue='high', priority=100); \
		print('Enqueueing 2 low-priority jobs...'); \
		for i in range(2): \
			fast_task.enqueue(number=200+i, queue='low', priority=-10); \
		print('✓ Demo jobs enqueued!'); \
	"
	@echo "$(GREEN)✓ Demo jobs enqueued$(NC)"
	@echo "$(YELLOW)  Start worker: make worker$(NC)"
	@echo "$(YELLOW)  Check status: make jobs-status$(NC)"

.PHONY: jobs-status
jobs-status: ## Show job queue status
	@echo "$(CYAN)Job Queue Status:$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery.models import QueuedJob; \
		from django.db.models import Count; \
		status_counts = QueuedJob.objects.values('status', 'queue_name').annotate(count=Count('id')).order_by('queue_name', 'status'); \
		print(''); \
		for item in status_counts: \
			queue = item['queue_name']; \
			status = item['status']; \
			count = item['count']; \
			print(f'  {queue:15} | {status:10} | {count:5} jobs'); \
		print(''); \
		total = QueuedJob.objects.count(); \
		print(f'  Total: {total} jobs'); \
	"

.PHONY: jobs-list
jobs-list: ## Show detailed list of all jobs
	@echo "$(CYAN)All Jobs (detailed):$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery.models import QueuedJob; \
		jobs = QueuedJob.objects.all().order_by('-created_at')[:50]; \
		print(''); \
		print(f'{'ID':>5} | {'Status':10} | {'Queue':10} | {'Task':40} | {'Tags':20} | {'Deps':5}'); \
		print('-' * 110); \
		for job in jobs: \
			task_short = job.task_path.split('.')[-1][:40]; \
			tags_str = ','.join(job.tags[:2]) if job.tags else ''; \
			deps = str(len(job.dependencies)) if job.dependencies else '0'; \
			print(f'{job.id:>5} | {job.status:10} | {job.queue_name:10} | {task_short:40} | {tags_str:20} | {deps:>5}'); \
		print(''); \
		print(f'Showing latest 50 jobs (total: {QueuedJob.objects.count()})'); \
	"

.PHONY: jobs-view
jobs-view: ## View specific job details (e.g., make jobs-view JOB_ID=123)
	$(call require-param,JOB_ID,$(JOB_ID),make jobs-view JOB_ID=<job_id>)
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery.models import QueuedJob; \
		import json; \
		job = QueuedJob.objects.get(id=$(JOB_ID)); \
		print(''); \
		print('Job Details:'); \
		print(f'  ID: {job.id}'); \
		print(f'  Task: {job.task_path}'); \
		print(f'  Status: {job.status}'); \
		print(f'  Queue: {job.queue_name}'); \
		print(f'  Priority: {job.priority}'); \
		print(f'  Created: {job.created_at}'); \
		print(f'  Started: {job.started_at}'); \
		print(f'  Finished: {job.finished_at}'); \
		print(f'  Duration: {job.duration_seconds}s' if job.duration_seconds else '  Duration: N/A'); \
		print(f'  Tags: {job.tags}'); \
		print(f'  Dependencies: {job.dependencies}'); \
		if job.webhook_url: \
			print(f'  Webhook: {job.webhook_url}'); \
			print(f'  Webhook Status: {job.webhook_status}'); \
		if job.kwargs: \
			print(f'  Arguments: {json.dumps(job.kwargs, indent=2)}'); \
		if job.output: \
			print(f'  Output: {job.output[:200]}'); \
		if job.error: \
			print(f'  Error: {job.error[:200]}'); \
		print(''); \
	"

.PHONY: populate-db
populate-db: ## Populate database with sample jobs in various states
	@echo "$(BLUE)→ Populating database with sample jobs...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task, slow_task; \
		from sqlery.models import QueuedJob; \
		print(''); \
		print('Creating sample jobs:'); \
		print('  - 10 queued jobs (various queues)'); \
		for i in range(5): \
			fast_task.enqueue(number=i, queue='default'); \
		for i in range(3): \
			fast_task.enqueue(number=100+i, queue='high', priority=100); \
		for i in range(2): \
			slow_task.enqueue(seconds=5, number=200+i, queue='low', priority=-10); \
		print('  - 5 jobs with tags (rate limiting)'); \
		for i in range(5): \
			fast_task.enqueue(number=300+i, tags=['api-test']); \
		print('  - 3 jobs with concurrency tags'); \
		for i in range(3): \
			slow_task.enqueue(seconds=3, number=400+i, tags=['slow-api']); \
		print('  - 3 chained jobs (dependencies)'); \
		job1 = fast_task.enqueue(number=500); \
		job2 = job1.then('tasks_app.tasks.fast_task', number=501); \
		job3 = job2.then('tasks_app.tasks.fast_task', number=502); \
		print('  - 2 jobs with webhooks'); \
		fast_task.enqueue( \
			number=600, \
			webhook_url='https://webhook.site/test', \
			webhook_events=['success'] \
		); \
		fast_task.enqueue( \
			number=601, \
			webhook_url='https://webhook.site/test', \
			webhook_events=['failure'] \
		); \
		print('  - Fan-out pattern (1 → 3)'); \
		root = fast_task.enqueue(number=700); \
		for i in range(3): \
			root.then('tasks_app.tasks.fast_task', number=700+i+1); \
		print(''); \
		total = QueuedJob.objects.filter(status='queued').count(); \
		print(f'✓ Created {total} queued jobs'); \
		print(''); \
		print('View them:'); \
		print('  make jobs-list      # Detailed list'); \
		print('  make jobs-status    # Status summary'); \
		print('  make jobs-view JOB_ID=1  # View specific job'); \
	"
	@echo "$(GREEN)✓ Database populated$(NC)"

.PHONY: populate-db-large
populate-db-large: ## Populate database with large dataset (100+ jobs)
	@echo "$(BLUE)→ Creating large dataset...$(NC)"
	@echo "$(YELLOW)  This will create 100+ jobs for load testing$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task, slow_task; \
		print(''); \
		print('Creating large dataset:'); \
		print('  - 50 fast jobs (default queue)'); \
		for i in range(50): \
			fast_task.enqueue(number=i, queue='default'); \
		print('  - 20 high priority jobs'); \
		for i in range(20): \
			fast_task.enqueue(number=1000+i, queue='high', priority=100); \
		print('  - 20 low priority jobs'); \
		for i in range(20): \
			fast_task.enqueue(number=2000+i, queue='low', priority=-10); \
		print('  - 20 jobs with rate limit tags'); \
		for i in range(20): \
			fast_task.enqueue(number=3000+i, tags=['api-limited']); \
		print('  - 10 slow jobs with concurrency limits'); \
		for i in range(10): \
			slow_task.enqueue(seconds=3, number=4000+i, tags=['concurrent-limit']); \
		print(''); \
		from sqlery.models import QueuedJob; \
		total = QueuedJob.objects.filter(status='queued').count(); \
		print(f'✓ Created {total} queued jobs'); \
	"
	@echo "$(GREEN)✓ Large dataset created$(NC)"
	@echo "$(YELLOW)  Process with: make workers-parallel NUM=4$(NC)"

.PHONY: populate-db-states
populate-db-states: ## Create jobs in different states (queued, running, success, failed)
	@echo "$(BLUE)→ Creating jobs in various states...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task, failing_task; \
		from sqlery.models import QueuedJob; \
		from django.utils import timezone; \
		print(''); \
		print('Creating jobs in different states:'); \
		print('  - 5 queued jobs'); \
		for i in range(5): \
			fast_task.enqueue(number=i); \
		print('  - Creating 3 successful jobs (simulated)'); \
		for i in range(3): \
			job = fast_task.enqueue(number=100+i); \
			job.status = 'success'; \
			job.started_at = timezone.now(); \
			job.finished_at = timezone.now(); \
			job.duration_seconds = 0.1; \
			job.output = f'Completed successfully: {100+i}'; \
			job.save(); \
		print('  - Creating 2 failed jobs (simulated)'); \
		for i in range(2): \
			job = fast_task.enqueue(number=200+i); \
			job.status = 'failed'; \
			job.started_at = timezone.now(); \
			job.finished_at = timezone.now(); \
			job.duration_seconds = 0.05; \
			job.error = f'Simulated error for job {200+i}'; \
			job.save(); \
		print('  - Creating 1 running job (simulated)'); \
		job = fast_task.enqueue(number=300); \
		job.status = 'running'; \
		job.started_at = timezone.now(); \
		job.save(); \
		print(''); \
		counts = QueuedJob.objects.values('status').annotate(count=Count('id')); \
		from django.db.models import Count; \
		counts = QueuedJob.objects.values('status').annotate(count=Count('id')); \
		for item in counts: \
			print(f'  {item[\"status\"]}: {item[\"count\"]} jobs'); \
		print(''); \
		print('✓ Database populated with various states'); \
	"
	@echo "$(GREEN)✓ Jobs in various states created$(NC)"

.PHONY: jobs-clear
jobs-clear: ## Clear all queued/failed jobs (WARNING)
	$(call warning-prompt,This will clear all queued and failed jobs)
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery.models import QueuedJob; \
		deleted = QueuedJob.objects.filter(status__in=['queued', 'failed']).delete(); \
		print(f'Deleted {deleted[0]} jobs'); \
	"
	@echo "$(GREEN)✓ Jobs cleared$(NC)"

# =============================================================================
# Docker
# =============================================================================

.PHONY: docker-build
docker-build: ## Build Docker image
	@cd $(SAMPLE_PROJECT) && docker build -t sqlery-demo .

.PHONY: docker-up
docker-up: ## Start Docker Compose stack
	@cd $(SAMPLE_PROJECT) && docker compose up -d
	@echo "$(GREEN)✓ Docker stack started$(NC)"
	@echo "$(YELLOW)  View logs: make docker-logs$(NC)"

.PHONY: docker-down
docker-down: ## Stop Docker Compose stack
	@cd $(SAMPLE_PROJECT) && docker compose down

.PHONY: docker-logs
docker-logs: ## View Docker Compose logs
	@cd $(SAMPLE_PROJECT) && docker compose logs -f

.PHONY: docker-shell
docker-shell: ## Open shell in Docker container
	@cd $(SAMPLE_PROJECT) && docker compose exec web bash

# =============================================================================
# Logs & Monitoring
# =============================================================================

.PHONY: logs
logs: ## Tail all worker logs
	@if [ -d "$(LOG_DIR)" ] && [ -n "$$(ls $(LOG_DIR)/worker-*.log 2>/dev/null)" ]; then \
		tail -f $(LOG_DIR)/worker-*.log; \
	else \
		echo "$(YELLOW)No worker logs found$(NC)"; \
	fi

.PHONY: logs-worker-high
logs-worker-high: ## Tail high priority worker log
	@tail -f $(LOG_DIR)/worker-high.log

.PHONY: logs-worker-default
logs-worker-default: ## Tail default priority worker log
	@tail -f $(LOG_DIR)/worker-default.log

.PHONY: logs-worker-low
logs-worker-low: ## Tail low priority worker log
	@tail -f $(LOG_DIR)/worker-low.log

# =============================================================================
# Cleanup & Utilities
# =============================================================================

.PHONY: clean
clean: workers-stop ## Clean generated files and stop workers
	@echo "$(BLUE)→ Cleaning up...$(NC)"
	@rm -rf $(LOG_DIR)
	@rm -rf $(PID_DIR)
	@rm -rf $(CURRENT_CONFIG)
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

.PHONY: clean-all
clean-all: clean ## Deep clean (including venv and database)
	$(call warning-prompt,This will delete venv and database)
	@rm -rf $(VENV)
	@rm -rf $(CONFIG_DIR)
	@rm -f $(SAMPLE_PROJECT)/db.sqlite3
	@echo "$(GREEN)✓ Deep clean complete$(NC)"

# =============================================================================
# Immediate/Synchronous Execution
# =============================================================================

.PHONY: run-task
run-task: ## Run a task immediately without enqueueing (e.g., make run-task TASK=tasks_app.tasks.fast_task)
	$(call require-param,TASK,$(TASK),make run-task TASK=tasks_app.tasks.fast_task ARGS='seconds=5')
	@echo "$(BLUE)→ Running task immediately: $(TASK)$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		import importlib; \
		module_path, func_name = '$(TASK)'.rsplit('.', 1); \
		module = importlib.import_module(module_path); \
		func = getattr(module, func_name); \
		args = {}; \
		$(if $(ARGS),args = {$(ARGS)};,) \
		result = func(**args); \
		print(f'Result: {result}'); \
	"

.PHONY: run-job-sync
run-job-sync: ## Run a specific job synchronously (e.g., make run-job-sync JOB_ID=123)
	$(call require-param,JOB_ID,$(JOB_ID),make run-job-sync JOB_ID=<job_id>)
	@echo "$(BLUE)→ Executing job $(JOB_ID) synchronously...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery.models import QueuedJob; \
		from sqlery.executor import execute_job; \
		job = QueuedJob.objects.get(id=$(JOB_ID)); \
		print(f'Job: {job.task_path}'); \
		execute_job(job); \
		job.refresh_from_db(); \
		print(f'Status: {job.status}'); \
		if job.output: \
			print(f'Output: {job.output}'); \
		if job.error: \
			print(f'Error: {job.error}'); \
	"

.PHONY: test-immediate-execution
test-immediate-execution: ## Demo immediate task execution
	$(call print-banner,Demo: Immediate Task Execution)
	@echo "$(YELLOW)1. Running task directly (no queue)$(NC)"
	@$(MAKE) -s run-task TASK=tasks_app.tasks.fast_task ARGS="'number':42"
	@echo ""
	@echo "$(YELLOW)2. Enqueue a job$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import slow_task; \
		job = slow_task.enqueue(seconds=2); \
		print(f'Created job {job.id}'); \
	" | tail -1 > /tmp/job_id.txt
	@echo ""
	@echo "$(YELLOW)3. Execute the queued job synchronously$(NC)"
	@$(MAKE) -s run-job-sync JOB_ID=$$(cat /tmp/job_id.txt | grep -oE '[0-9]+')
	@rm -f /tmp/job_id.txt

# =============================================================================
# Rate Limiting Tests
# =============================================================================

.PHONY: test-rate-limiting
test-rate-limiting: ## Demo rate limiting with tagged jobs
	$(call print-banner,Demo: Rate Limiting)
	@echo "$(YELLOW)This demonstrates tag-based rate limiting:$(NC)"
	@echo "  - Tag: 'test-api' with limit '10/m' (10 per minute)"
	@echo "  - Enqueueing 20 jobs"
	@echo "  - Worker will process only 10/minute"
	@echo ""
	@echo "$(BLUE)→ Enqueueing 20 jobs with 'test-api' tag...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		for i in range(20): \
			fast_task.enqueue(number=i, tags=['test-api']); \
		print('✓ Enqueued 20 jobs with rate limit tag'); \
	"
	@echo ""
	@echo "$(YELLOW)Start worker with rate limit:$(NC)"
	@echo "  SQLERY_TAG_RATE_LIMITS='{\"test-api\": \"10/m\"}' make worker"
	@echo ""
	@echo "$(YELLOW)Check status:$(NC)"
	@echo "  make jobs-status"

.PHONY: worker-rate-limited
worker-rate-limited: ## Start worker with rate limit example (10/minute on 'test-api')
	@echo "$(BLUE)→ Starting worker with rate limit: test-api=10/m$(NC)"
	@SQLERY_TAG_RATE_LIMITS='{"test-api": "10/m"}' $(DJANGO_MANAGE) run_jobs --verbosity=2

.PHONY: demo-rate-limiting-full
demo-rate-limiting-full: ## Complete rate limiting demo (enqueue + worker)
	$(call print-banner,Complete Rate Limiting Demo)
	@echo "$(YELLOW)1. Clearing old jobs$(NC)"
	$(call clear-queued-jobs)
	@echo ""
	@echo "$(YELLOW)2. Enqueueing 30 jobs with 'api-limit' tag$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		for i in range(30): \
			fast_task.enqueue(number=i, tags=['api-limit']); \
		print('✓ Enqueued 30 jobs'); \
	"
	@echo ""
	@echo "$(YELLOW)3. Starting worker with 5/minute rate limit$(NC)"
	@echo "   Watch: only 5 jobs will process per minute"
	@echo "   Press Ctrl+C to stop"
	@echo ""
	@SQLERY_TAG_RATE_LIMITS='{"api-limit": "5/m"}' $(DJANGO_MANAGE) run_jobs --verbosity=2

# =============================================================================
# Concurrency Limit Tests
# =============================================================================

.PHONY: test-concurrency-limits
test-concurrency-limits: ## Demo concurrency limiting with tagged jobs
	$(call print-banner,Demo: Concurrency Limiting)
	@echo "$(YELLOW)This demonstrates tag-based concurrency limiting:$(NC)"
	@echo "  - Tag: 'slow-api' with concurrency limit: 2"
	@echo "  - Enqueueing 10 slow jobs (5 seconds each)"
	@echo "  - Only 2 will run concurrently"
	@echo ""
	@echo "$(BLUE)→ Enqueueing 10 slow jobs with 'slow-api' tag...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import slow_task; \
		for i in range(10): \
			slow_task.enqueue(seconds=5, number=i, tags=['slow-api']); \
		print('✓ Enqueued 10 slow jobs (5s each) with concurrency limit'); \
	"
	@echo ""
	@echo "$(YELLOW)Start multiple workers with concurrency limit:$(NC)"
	@echo "  SQLERY_TAG_CONCURRENCY_LIMITS='{\"slow-api\": 2}' make workers-parallel NUM=4"
	@echo ""
	@echo "$(YELLOW)Even with 4 workers, only 2 'slow-api' jobs will run at once!$(NC)"

.PHONY: workers-concurrency-limited
workers-concurrency-limited: ## Start 4 workers with concurrency limit (slow-api=2)
	@echo "$(BLUE)→ Starting 4 workers with concurrency limit: slow-api=2$(NC)"
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	@for i in $$(seq 1 4); do \
		echo "  $(CYAN)Starting worker $$i...$(NC)"; \
		SQLERY_TAG_CONCURRENCY_LIMITS='{"slow-api": 2}' \
		$(DJANGO_MANAGE) run_jobs --verbosity=2 > $(LOG_DIR)/worker-$$i.log 2>&1 & \
		echo $$! > $(PID_DIR)/worker-$$i.pid; \
	done
	@echo "$(GREEN)✓ Started 4 workers with concurrency limit$(NC)"
	@echo "$(YELLOW)  Max 2 'slow-api' jobs will run concurrently$(NC)"
	@echo "$(YELLOW)  Stop: make workers-stop$(NC)"

.PHONY: demo-concurrency-full
demo-concurrency-full: ## Complete concurrency limiting demo
	$(call print-banner,Complete Concurrency Limiting Demo)
	@echo "$(YELLOW)1. Clearing old jobs$(NC)"
	$(call clear-queued-jobs)
	@echo ""
	@echo "$(YELLOW)2. Enqueueing 8 slow jobs (3 seconds each)$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import slow_task; \
		for i in range(8): \
			slow_task.enqueue(seconds=3, number=i, tags=['concurrency-test']); \
		print('✓ Enqueued 8 jobs'); \
	"
	@echo ""
	@echo "$(YELLOW)3. Starting 4 workers with concurrency limit=2$(NC)"
	@echo "   Watch: only 2 jobs run at once despite 4 workers!"
	@echo ""
	@$(MAKE) -s workers-concurrency-limited
	@sleep 2
	@echo ""
	@echo "$(YELLOW)4. Monitoring (press Ctrl+C to continue)$(NC)"
	@$(MAKE) -s jobs-status
	@echo ""
	@read -p "Press Enter to stop workers..." dummy
	@$(MAKE) -s workers-stop

# =============================================================================
# Webhook Tests
# =============================================================================

.PHONY: test-webhooks
test-webhooks: ## Demo webhook notifications (requires webhook receiver)
	$(call print-banner,Demo: Webhook Notifications)
	@echo "$(YELLOW)This demonstrates webhook notifications:$(NC)"
	@echo "  - Enqueue job with webhook URL"
	@echo "  - On completion, POST to webhook URL"
	@echo "  - Includes HMAC signature for security"
	@echo ""
	@echo "$(BLUE)→ Prerequisites:$(NC)"
	@echo "  1. Start webhook receiver:"
	@echo "     python -m http.server 8080"
	@echo "  2. Or use webhook.site for testing"
	@echo ""
	@echo "$(BLUE)→ Enqueueing job with webhook...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		job = fast_task.enqueue( \
			number=42, \
			webhook_url='https://webhook.site/unique-id', \
			webhook_events=['success', 'failure'] \
		); \
		print(f'✓ Created job {job.id} with webhook'); \
		print(f'  Webhook URL: {job.webhook_url}'); \
	"
	@echo ""
	@echo "$(YELLOW)Process the job:$(NC)"
	@echo "  make worker-once"

.PHONY: demo-webhook-success
demo-webhook-success: ## Demo webhook on successful job
	@echo "$(BLUE)→ Creating job with webhook (will succeed)$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		job = fast_task.enqueue( \
			number=100, \
			webhook_url='https://webhook.site/YOUR-UNIQUE-ID', \
			webhook_events=['success'] \
		); \
		print(f'Job ID: {job.id}'); \
	"
	@echo ""
	@echo "$(YELLOW)Processing job...$(NC)"
	@$(DJANGO_MANAGE) run_jobs --once --verbosity=2

.PHONY: demo-webhook-failure
demo-webhook-failure: ## Demo webhook on failed job
	@echo "$(BLUE)→ Creating job with webhook (will fail)$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import failing_task; \
		job = failing_task.enqueue( \
			webhook_url='https://webhook.site/YOUR-UNIQUE-ID', \
			webhook_events=['failure'] \
		); \
		print(f'Job ID: {job.id}'); \
	"
	@echo ""
	@echo "$(YELLOW)Processing job...$(NC)"
	@$(DJANGO_MANAGE) run_jobs --once --verbosity=2

# =============================================================================
# Job Dependencies Tests
# =============================================================================

.PHONY: test-job-dependencies
test-job-dependencies: ## Demo job dependencies and chaining
	$(call print-banner,Demo: Job Dependencies)
	@echo "$(YELLOW)This demonstrates job dependency chaining:$(NC)"
	@echo "  - Job 2 depends on Job 1"
	@echo "  - Job 3 depends on Job 2"
	@echo "  - Sequential execution guaranteed"
	@echo ""
	@echo "$(BLUE)→ Creating job chain...$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		job1 = fast_task.enqueue(number=1); \
		print(f'Job 1: {job1.id}'); \
		job2 = job1.then('tasks_app.tasks.fast_task', number=2); \
		print(f'Job 2: {job2.id} (depends on {job1.id})'); \
		job3 = job2.then('tasks_app.tasks.fast_task', number=3); \
		print(f'Job 3: {job3.id} (depends on {job2.id})'); \
		print(''); \
		print('✓ Created 3-job chain'); \
	"
	@echo ""
	@echo "$(YELLOW)Process jobs:$(NC)"
	@echo "  make worker"

.PHONY: demo-dependencies-fan-out
demo-dependencies-fan-out: ## Demo fan-out pattern (1 → many)
	$(call print-banner,Demo: Fan-Out Pattern)
	@echo "$(YELLOW)Creating fan-out: 1 root job → 3 parallel jobs$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		root = fast_task.enqueue(number=0); \
		print(f'Root job: {root.id}'); \
		for i in range(1, 4): \
			child = root.then('tasks_app.tasks.fast_task', number=i); \
			print(f'  Child {i}: {child.id}'); \
		print(''); \
		print('✓ Created fan-out: 1 → 3'); \
	"
	@echo ""
	@echo "$(YELLOW)Start workers to process:$(NC)"
	@echo "  make workers-parallel NUM=3"

.PHONY: demo-dependencies-fan-in
demo-dependencies-fan-in: ## Demo fan-in pattern (many → 1)
	$(call print-banner,Demo: Fan-In Pattern)
	@echo "$(YELLOW)Creating fan-in: 3 parallel jobs → 1 final job$(NC)"
	@$(DJANGO_MANAGE) shell -c " \
		from sqlery import enqueue; \
		job1 = enqueue('tasks_app.tasks.fast_task', number=1); \
		job2 = enqueue('tasks_app.tasks.fast_task', number=2); \
		job3 = enqueue('tasks_app.tasks.fast_task', number=3); \
		print(f'Job 1: {job1.id}'); \
		print(f'Job 2: {job2.id}'); \
		print(f'Job 3: {job3.id}'); \
		final = enqueue('tasks_app.tasks.fast_task', number=999, depends_on=[job1.id, job2.id, job3.id]); \
		print(f'Final job: {final.id} (depends on all 3)'); \
		print(''); \
		print('✓ Created fan-in: 3 → 1'); \
	"
	@echo ""
	@echo "$(YELLOW)Process jobs:$(NC)"
	@echo "  make worker"

# =============================================================================
# Combined/Advanced Tests
# =============================================================================

.PHONY: demo-full-pipeline
demo-full-pipeline: ## Demo complete ETL pipeline with all features
	$(call print-banner,Demo: Full ETL Pipeline)
	@echo "$(YELLOW)Creating ETL pipeline with:$(NC)"
	@echo "  - Job dependencies (extract → transform → load)"
	@echo "  - Rate limiting (API calls)"
	@echo "  - Webhooks (completion notification)"
	@echo ""
	@$(DJANGO_MANAGE) shell -c " \
		from tasks_app.tasks import fast_task; \
		extract = fast_task.enqueue( \
			number=1, \
			tags=['api-extract'], \
		); \
		print(f'Extract job: {extract.id}'); \
		transform = extract.then( \
			'tasks_app.tasks.slow_task', \
			seconds=2, \
			tags=['cpu-intensive'] \
		); \
		print(f'Transform job: {transform.id}'); \
		load = transform.then( \
			'tasks_app.tasks.fast_task', \
			number=3, \
			webhook_url='https://webhook.site/unique-id', \
			webhook_events=['success', 'failure'] \
		); \
		print(f'Load job: {load.id} (with webhook)'); \
		print(''); \
		print('✓ Created ETL pipeline'); \
	"
	@echo ""
	@echo "$(YELLOW)Start worker with limits:$(NC)"
	@echo "  SQLERY_TAG_RATE_LIMITS='{\"api-extract\": \"10/m\"}' \\"
	@echo "  SQLERY_TAG_CONCURRENCY_LIMITS='{\"cpu-intensive\": 2}' \\"
	@echo "  make worker"

# =============================================================================
# Example Workflows
# =============================================================================

.PHONY: example-basic
example-basic: ## Example: Basic single worker workflow
	$(call print-banner,Example: Basic Single Worker Workflow)
	@echo "$(YELLOW)1. Setup$(NC)"
	@echo "   make setup"
	@echo ""
	@echo "$(YELLOW)2. Enqueue demo jobs$(NC)"
	@echo "   make demo-jobs"
	@echo ""
	@echo "$(YELLOW)3. Start worker$(NC)"
	@echo "   make worker"
	@echo ""
	@echo "$(YELLOW)4. Check status$(NC)"
	@echo "   make jobs-status"
	@echo ""

.PHONY: example-multi-worker
example-multi-worker: ## Example: Multi-worker parallel processing
	$(call print-banner,Example: Multi-Worker Parallel Processing)
	@echo "$(YELLOW)1. Setup$(NC)"
	@echo "   make setup"
	@echo ""
	@echo "$(YELLOW)2. Start 4 parallel workers$(NC)"
	@echo "   make workers-parallel NUM=4"
	@echo ""
	@echo "$(YELLOW)3. Enqueue many jobs$(NC)"
	@echo "   make demo-jobs"
	@echo ""
	@echo "$(YELLOW)4. Monitor workers$(NC)"
	@echo "   make workers-status"
	@echo "   make logs"
	@echo ""
	@echo "$(YELLOW)5. Stop workers$(NC)"
	@echo "   make workers-stop"
	@echo ""

.PHONY: example-queue-separation
example-queue-separation: ## Example: Separate workers per queue
	$(call print-banner,Example: Queue Separation)
	@echo "$(YELLOW)1. Setup$(NC)"
	@echo "   make setup"
	@echo ""
	@echo "$(YELLOW)2. Start queue-specific workers$(NC)"
	@echo "   make workers-separate-queues"
	@echo ""
	@echo "$(YELLOW)3. Enqueue jobs to different queues$(NC)"
	@echo "   make demo-jobs"
	@echo ""
	@echo "$(YELLOW)4. Monitor specific queue$(NC)"
	@echo "   make logs-worker-high"
	@echo ""
	@echo "$(YELLOW)5. Check status$(NC)"
	@echo "   make workers-status"
	@echo "   make jobs-status"
	@echo ""

# Default target
.DEFAULT_GOAL := help
