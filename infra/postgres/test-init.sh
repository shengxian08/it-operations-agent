#!/bin/sh
set -eu
for database in itops_e2e itops_schema_test itops_restore_test; do
  createdb --username "$POSTGRES_USER" "$database"
done
