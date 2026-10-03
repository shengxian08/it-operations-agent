#!/bin/bash
set -euo pipefail
export KC_DB_PASSWORD="$(cat /run/secrets/keycloak_db_password)"
export KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /run/secrets/keycloak_admin_password)"
export OIDC_CLIENT_SECRET="$(cat /run/secrets/oidc_client_secret)"
exec /opt/keycloak/bin/kc.sh start --import-realm
