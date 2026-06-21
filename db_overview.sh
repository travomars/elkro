#!/usr/bin/env bash
# Elkro DB overview — row counts + latest 10 entries per table.
# Usage: ./db_overview.sh [container_name]

CONTAINER="${1:-elkro-db}"
DB="elkro"
USER="postgres"

run() {
  docker exec -i "$CONTAINER" psql -U "$USER" -d "$DB" -c "$1"
}

echo "=============================================="
echo " ELKRO DB OVERVIEW"
echo "=============================================="

echo -e "\n--- Row counts ---"
run "
SELECT 'memories' AS table, COUNT(*) FROM memories
UNION ALL SELECT 'summaries', COUNT(*) FROM summaries
UNION ALL SELECT 'facts', COUNT(*) FROM facts
UNION ALL SELECT 'share_requests', COUNT(*) FROM share_requests
UNION ALL SELECT 'identities', COUNT(*) FROM identities;
"

echo -e "\n--- Latest 10 memories ---"
run "
SELECT id, project_id, scope, LEFT(content, 60) AS content, created_at
FROM memories ORDER BY created_at DESC LIMIT 10;
"

echo -e "\n--- Latest 10 summaries ---"
run "
SELECT id, project_id, LEFT(content, 80) AS content, created_at
FROM summaries ORDER BY created_at DESC LIMIT 10;
"

echo -e "\n--- Latest 10 facts ---"
run "
SELECT project_id, key, value, updated_at
FROM facts ORDER BY updated_at DESC LIMIT 10;
"

echo -e "\n--- Latest 10 share_requests ---"
run "
SELECT id, project_id, target_project_id, status, LEFT(content, 60) AS content, proposed_at
FROM share_requests ORDER BY proposed_at DESC LIMIT 10;
"

echo -e "\n--- Latest 10 identities ---"
run "
SELECT platform, platform_id, canonical_id, id
FROM identities ORDER BY id DESC LIMIT 10;
"

echo -e "\nDone."
