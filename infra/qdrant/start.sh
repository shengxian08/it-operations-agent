#!/bin/sh
set -eu
export QDRANT__SERVICE__API_KEY="$(cat /run/secrets/qdrant_api_key)"
export QDRANT__SERVICE__READ_ONLY_API_KEY="$(cat /run/secrets/qdrant_read_api_key)"
exec /qdrant/qdrant
