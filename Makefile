# Bug Database — automation for local dev, infra, and production deploy.
#
# Quick reference:
#   make help              list all targets
#   make gen-key           print a fresh random API key
#   make set-key           generate a key and write it into deploy/.env.prod
#   make rotate-key        generate a new key, update server, restart API
#   make tf-apply          provision the Yandex Cloud VM (Terraform)
#   make deploy            build + (re)start the prod stack on the VM
#   make logs / ps / down  operate the remote stack
#
# Config: edit deploy/.env.prod (see deploy/.env.prod.example). SSH_HOST is
# taken from Terraform output automatically, or override: make deploy SSH_HOST=1.2.3.4

SHELL       := /bin/bash
ENV_PROD    := deploy/.env.prod
COMPOSE     := docker compose --env-file $(ENV_PROD) -f deploy/docker-compose.prod.yml
SSH_USER    ?= ubuntu
REMOTE_DIR  ?= /srv/bugdb
TF_DIR      := deploy/terraform

# Resolve the VM IP from Terraform output unless SSH_HOST is passed in.
SSH_HOST ?= $(shell terraform -chdir=$(TF_DIR) output -raw public_ip 2>/dev/null)
SSH      := ssh $(SSH_USER)@$(SSH_HOST)

.DEFAULT_GOAL := help

# --------------------------------------------------------------------------- #
# Help
# --------------------------------------------------------------------------- #
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# Local development
# --------------------------------------------------------------------------- #
.PHONY: venv
venv: ## Create local virtualenv and install deps
	python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

.PHONY: dev
dev: ## Run the API locally with auto-reload (http://localhost:8000)
	./.venv/bin/python index.py

.PHONY: build
build: ## Build the Docker image locally
	docker build -t bugdb:latest .

# --------------------------------------------------------------------------- #
# API key management
# --------------------------------------------------------------------------- #
.PHONY: gen-key
gen-key: ## Print a fresh random API key (does not save it)
	@python -c "import secrets; print(secrets.token_urlsafe(32))"

.PHONY: set-key
set-key: ## Generate a key and write API_KEY into deploy/.env.prod
	@test -f $(ENV_PROD) || cp deploy/.env.prod.example $(ENV_PROD)
	@key=$$(python -c "import secrets; print(secrets.token_urlsafe(32))"); \
	if grep -q '^API_KEY=' $(ENV_PROD); then \
		sed -i.bak "s|^API_KEY=.*|API_KEY=$$key|" $(ENV_PROD) && rm -f $(ENV_PROD).bak; \
	else \
		echo "API_KEY=$$key" >> $(ENV_PROD); \
	fi; \
	echo "Wrote new API_KEY to $(ENV_PROD)"; \
	echo "  $$key"

.PHONY: show-key
show-key: ## Print the API key currently in deploy/.env.prod
	@grep '^API_KEY=' $(ENV_PROD) | cut -d= -f2-

.PHONY: rotate-key
rotate-key: set-key ## Generate a NEW key, push it to the server, restart API
	@echo ">> Rotating key on $(SSH_HOST) ..."
	@scp $(ENV_PROD) $(SSH_USER)@$(SSH_HOST):$(REMOTE_DIR)/deploy/.env.prod
	@$(SSH) "cd $(REMOTE_DIR) && docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml up -d api"
	@echo ">> Done. New key is active. Update your clients (BUGDB_API_KEY)."

# --------------------------------------------------------------------------- #
# Infrastructure (Terraform / Yandex Cloud)
# --------------------------------------------------------------------------- #
.PHONY: tf-init
tf-init: ## terraform init
	terraform -chdir=$(TF_DIR) init

.PHONY: tf-plan
tf-plan: ## terraform plan
	terraform -chdir=$(TF_DIR) plan

.PHONY: tf-apply
tf-apply: ## Provision the VM (terraform apply)
	terraform -chdir=$(TF_DIR) apply

.PHONY: tf-output
tf-output: ## Show Terraform outputs (public IP, ssh command)
	terraform -chdir=$(TF_DIR) output

.PHONY: tf-destroy
tf-destroy: ## Tear down all cloud resources
	terraform -chdir=$(TF_DIR) destroy

# --------------------------------------------------------------------------- #
# Deploy to the VM
# --------------------------------------------------------------------------- #
.PHONY: check-host
check-host:
	@test -n "$(SSH_HOST)" || { echo "ERROR: SSH_HOST empty. Run 'make tf-apply' first or pass SSH_HOST=<ip>."; exit 1; }
	@test -f $(ENV_PROD) || { echo "ERROR: $(ENV_PROD) missing. Run 'make set-key' and edit DOMAIN/ACME_EMAIL."; exit 1; }

.PHONY: sync
sync: check-host ## Copy the repo to the VM (rsync, excludes junk)
	@echo ">> Syncing to $(SSH_HOST):$(REMOTE_DIR) ..."
	@$(SSH) "sudo mkdir -p $(REMOTE_DIR) && sudo chown -R $(SSH_USER) $(REMOTE_DIR)"
	@rsync -az --delete \
		--exclude '.venv' --exclude 'data' --exclude '.git' \
		--exclude '__pycache__' --exclude '*.pyc' \
		./ $(SSH_USER)@$(SSH_HOST):$(REMOTE_DIR)/

.PHONY: deploy
deploy: sync ## Build + (re)start the prod stack on the VM
	@echo ">> Building and starting stack on $(SSH_HOST) ..."
	@$(SSH) "cd $(REMOTE_DIR) && docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml up -d --build"
	@echo ">> Deployed. Check: make ping"

.PHONY: ping
ping: check-host ## Curl the public health endpoint over HTTPS
	@domain=$$(grep '^DOMAIN=' $(ENV_PROD) | cut -d= -f2-); \
	echo ">> GET https://$$domain/health"; \
	curl -fsS "https://$$domain/health" && echo

.PHONY: logs
logs: check-host ## Tail remote container logs
	@$(SSH) "cd $(REMOTE_DIR) && docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml logs -f --tail=100"

.PHONY: ps
ps: check-host ## Show remote container status
	@$(SSH) "cd $(REMOTE_DIR) && docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml ps"

.PHONY: down
down: check-host ## Stop the remote stack
	@$(SSH) "cd $(REMOTE_DIR) && docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml down"

.PHONY: ssh
ssh: check-host ## Open an SSH session to the VM
	@$(SSH)

# --------------------------------------------------------------------------- #
# Backups (run on the VM)
# --------------------------------------------------------------------------- #
.PHONY: backup
backup: check-host ## Run a one-off DB backup on the VM
	@$(SSH) "sudo DATA_DIR=/srv/data BACKUP_DIR=/srv/backups $(REMOTE_DIR)/scripts/backup.sh"

.PHONY: install-backup-cron
install-backup-cron: check-host ## Install the daily backup cron on the VM
	@$(SSH) "sudo bash $(REMOTE_DIR)/scripts/install-backup-cron.sh"

.PHONY: fetch-backups
fetch-backups: check-host ## Download remote backups into ./backups
	@mkdir -p backups
	@rsync -az -e ssh "$(SSH_USER)@$(SSH_HOST):/srv/backups/" ./backups/
	@echo ">> Backups synced to ./backups"
