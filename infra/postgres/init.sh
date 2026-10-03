#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname postgres --set=ON_ERROR_STOP=1 --set=app_password="$(cat /run/secrets/app_db_password)" --set=migration_password="$(cat /run/secrets/migration_db_password)" --set=identity_password="$(cat /run/secrets/keycloak_db_password)" <<'SQL'
CREATE USER itops_app PASSWORD :'app_password';
CREATE USER itops_migrator PASSWORD :'migration_password';
CREATE DATABASE itops OWNER itops_migrator;
CREATE USER keycloak PASSWORD :'identity_password';
CREATE DATABASE keycloak OWNER keycloak;
\connect itops
GRANT CONNECT ON DATABASE itops TO itops_app;
GRANT USAGE ON SCHEMA public TO itops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE itops_migrator IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO itops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE itops_migrator IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO itops_app;
SQL
