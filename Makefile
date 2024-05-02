# Makefile 

.PHONY: help venv setup dev stop

export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root
export POSTGRES_PASSWORD=dev

help:	## Show this help message
	@egrep -h '(\s##\s|^##\s)' $(MAKEFILE_LIST) | egrep -v '^--' | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[32m  %-10s\033[0m %s\n", $$1, $$2}'

venv:	## Setup Python virtual environment
	@python3 -m venv ./venv
	@./venv/bin/pip install --upgrade pip && ./venv/bin/pip install -r ./requirements.txt

setup:  ## Setup Docker container
	@docker run \
	  --name=postgres \
	  -p 5432:5432 \
	  -e POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
	  --net=bridge \
	  --detach \
	  --rm \
	  postgres:16-alpine > /dev/null

	@docker run \
	  --name=vault \
	  --cap-add=IPC_LOCK \
	  --net=bridge \
	  -p 8200:8200 \
	  -e 'VAULT_DEV_ROOT_TOKEN_ID=root' \
	  -e 'VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200' \
	  --detach \
	  --rm \
	  hashicorp/vault:1.16 > /dev/null

	@sleep 1

	@echo 'path "*" { capabilities = ["create", "read", "update", "delete", "list", "patch", "sudo"] }' | vault policy write admin -

	@vault auth enable approle
	@vault write auth/approle/role/demo token_policies=admin token_ttl=15s token_max_ttl=30s

	@vault read -field=role_id auth/approle/role/demo/role-id > ./roleid
	@vault write -f -field=secret_id auth/approle/role/demo/secret-id >> ./secretid

	@vault secrets enable -path=pgsql database

	@vault write pgsql/config/demo \
	  plugin_name="postgresql-database-plugin" \
	  allowed_roles="demo" \
	  connection_url="postgresql://{{username}}:{{password}}@$$(hostname -I | awk '{print $$1}'):5432/postgres" \
	  username="postgres" \
	  password=${POSTGRES_PASSWORD} \
	  password_authentication="scram-sha-256"

	@vault write pgsql/roles/demo \
	  db_name="demo" \
	  creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}' SUPERUSER;" \
	  default_ttl="10s" \
	  max_ttl="20s"

dev:	## Launch a dev server
	@if [ ! -d ./venv ]; then \
		make venv; \
	fi
	@if ! (docker ps -q --filter "name=postgres" | grep -q . && docker ps -q --filter "name=vault" | grep -q .); then \
		make setup; \
	fi
	@./venv/bin/python ./src/main.py

stop:	## Destroy demo environment
	@docker stop vault postgres
	@rm ./roleid ./secretid 2>&1 > /dev/null
