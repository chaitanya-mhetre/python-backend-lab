#!/usr/bin/env bash
# End-to-end tour of the API against a running server (make up && make migrate && make run,
# and `make worker` in another terminal to see the workflow run). Needs curl + jq.
set -euo pipefail
BASE=${BASE:-http://localhost:8000/api/v1}
EMAIL="demo-$RANDOM@example.com"
json() { curl -sf -H 'content-type: application/json' "$@"; }

json -X POST "$BASE/auth/register" -d "{\"email\":\"$EMAIL\",\"password\":\"demo-password-1\",\"full_name\":\"Demo\"}" >/dev/null
TOKEN=$(json -X POST "$BASE/auth/login" -d "{\"email\":\"$EMAIL\",\"password\":\"demo-password-1\"}" | jq -r .access_token)
AUTH=(-H "Authorization: Bearer $TOKEN")

ORG=$(json "${AUTH[@]}" -X POST "$BASE/orgs" -d '{"name":"Demo Org"}' | jq -r .id)
PROJECT=$(json "${AUTH[@]}" -X POST "$BASE/orgs/$ORG/projects" -d '{"name":"Launch"}' | jq -r .id)
echo "org=$ORG project=$PROJECT"

WF=$(json "${AUTH[@]}" -X POST "$BASE/orgs/$ORG/workflows" -d '{
  "name": "follow up", "trigger_type": "task.status_changed", "trigger_filter": {"to": "done"},
  "steps": [{"type": "create_task", "config": {"title": "Retro: {title}"}}]}' | jq -r .id)

TASK=$(json "${AUTH[@]}" -X POST "$BASE/projects/$PROJECT/tasks" -d '{"title":"Ship v1"}' | jq -r .id)
json "${AUTH[@]}" -X PATCH "$BASE/tasks/$TASK" -H 'If-Match: "0"' -d '{"status":"in_progress"}' | jq -c '{status,version}'
json "${AUTH[@]}" -X PATCH "$BASE/tasks/$TASK" -H 'If-Match: "1"' -d '{"status":"done"}' | jq -c '{status,version}'

sleep 2  # give the worker a moment
json "${AUTH[@]}" "$BASE/projects/$PROJECT/tasks?sort=created_at" | jq -r '.items[].title'
json "${AUTH[@]}" "$BASE/workflows/$WF/executions" | jq -c '.[] | {status, definition_version}'
json "${AUTH[@]}" "$BASE/orgs/$ORG/audit-logs?limit=5" | jq -c '.[] | {action, actor_type}'
