"""Custom Prometheus metrics (HTTP metrics come from prometheus-fastapi-instrumentator)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

WORKFLOW_EXECUTIONS = Counter(
    "flowforge_workflow_executions_total",
    "Workflow executions that reached a final state",
    ["status"],
)
STEP_DURATION = Histogram(
    "flowforge_workflow_step_seconds",
    "Time spent running one workflow step",
    ["step_type", "outcome"],
)
WEBHOOK_DELIVERIES = Counter(
    "flowforge_webhook_delivery_attempts_total",
    "Webhook delivery attempts",
    ["outcome"],
)
