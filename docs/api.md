# API guide

Interactive docs: run `make up && make migrate && make run`, open http://localhost:8000/docs.
Everything is under `/api/v1`. Errors always look like:
```json
{"error": {"code": "task_version_conflict", "message": "...", "request_id": "3f2c..."}}
```

| Status | When | Example codes |
|---|---|---|
| 401 | missing/invalid token or API key | `authentication_failed` |
| 403 | authenticated but not allowed | `permission_denied` |
| 404 | not found **or** not visible to you | `not_found` |
| 409 | conflicts | `task_version_conflict`, `invalid_status_transition`, `conflict` |
| 413 | body > 1 MB | `payload_too_large` |
| 422 | invalid input | `validation_error`, `invalid_step_config`, `unsafe_webhook_url`, `invalid_cursor` |
| 429 | rate limited (see `Retry-After`) | `rate_limited` |

## Walkthrough
`examples/api_walkthrough.sh` runs this whole flow against a local server.
```bash
# register + login
curl -X POST :8000/api/v1/auth/register -d '{"email":"a@x.io","password":"long-password-1","full_name":"A"}' -H 'content-type: application/json'
LOGIN=$(curl -s -X POST :8000/api/v1/auth/login -d '{"email":"a@x.io","password":"long-password-1"}' -H 'content-type: application/json')
TOKEN=$(echo "$LOGIN" | jq -r .access_token); REFRESH=$(echo "$LOGIN" | jq -r .refresh_token)

# when the access token expires: rotate (the old refresh token stops working)
curl -X POST :8000/api/v1/auth/refresh -d "{\"refresh_token\":\"$REFRESH\"}" -H 'content-type: application/json'
# log out everywhere this login reached
curl -X POST :8000/api/v1/auth/logout -d "{\"refresh_token\":\"$REFRESH\"}" -H 'content-type: application/json'

# org → project → task
ORG=$(curl -s -X POST :8000/api/v1/orgs -H "Authorization: Bearer $TOKEN" -d '{"name":"Acme"}' -H 'content-type: application/json' | jq -r .id)
PROJECT=$(curl -s -X POST :8000/api/v1/orgs/$ORG/projects ... -d '{"name":"Launch"}' | jq -r .id)
curl -X POST :8000/api/v1/projects/$PROJECT/tasks ... -d '{"title":"Write docs","priority":2}'

# list with filters, sort and cursor
curl ":8000/api/v1/projects/$PROJECT/tasks?status=todo&assignee=me&sort=-priority&limit=20"
#  -> {"items": [...], "next_cursor": "WyItcHJp..."}; pass ?cursor=... for the next page

# safe concurrent edit
curl -X PATCH :8000/api/v1/tasks/$TASK -H 'If-Match: "0"' -d '{"status":"in_progress"}'   # ETag: "1"
```

## Workflows
```json
POST /api/v1/orgs/{org}/workflows
{
  "name": "Follow up finished work",
  "trigger_type": "task.status_changed",
  "trigger_filter": {"to": "done"},
  "steps": [
    {"type": "create_task", "config": {"title": "Retro: {title}"}},
    {"type": "delay", "config": {"seconds": 86400}},
    {"type": "send_notification", "config": {"to": "creator", "message": "Retro for {title} is due"}},
    {"type": "call_webhook", "config": {"webhook_id": "…"}}
  ]
}
```
Step configs are validated when saved. `{title}`-style placeholders are filled from the event
payload. Inspect runs with `GET /workflows/{id}/executions` and `GET /executions/{id}` (includes
every step attempt); stop one with `POST /executions/{id}:cancel`.

## API keys
`POST /orgs/{org}/api-keys {"name": "ci", "scopes": ["task:read", "task:create"]}` returns the key
**once**. Use it as `Authorization: Bearer ff_live_…`. Keys only see their own org and only the
scoped actions; they can't call human-only endpoints (`/me`, creating orgs or keys).

## Webhooks
`POST /orgs/{org}/webhooks {"url": "https://…", "events": ["task.created"]}` returns the signing
secret once. Verify deliveries as in `examples/verify_webhook.py`. Retries: 1m, 5m, 30m, 2h, 12h.
Deduplicate on the payload `id` (redeliveries reuse it).
