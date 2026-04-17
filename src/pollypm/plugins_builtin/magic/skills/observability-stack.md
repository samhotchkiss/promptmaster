---
name: observability-stack
description: Logs, metrics, traces — pick a stack, instrument, dashboard, alert.
when_to_trigger:
  - observability
  - monitoring
  - tracing
  - metrics
  - logs
kind: magic_skill
attribution: https://github.com/open-telemetry/opentelemetry-specification
---

# Observability Stack

## When to use

Use when you have a service in production and no way to answer "what is happening?" at 3am. Or when you are rebuilding a stack from logs-only to proper observability. Skip for side projects with no users — you do not have a problem to observe yet.

## Process

1. **Pick the stack up front and commit.** Three shapes cover most orgs:
   - **Self-hosted**: Prometheus (metrics) + Loki (logs) + Tempo (traces) + Grafana. Cheap; you operate it.
   - **Managed open source**: Grafana Cloud — same shape, they operate it.
   - **All-in-one SaaS**: Datadog, Honeycomb, New Relic. Quick to start, expensive at scale.
   Do not mix-and-match unless there is a strong reason.
2. **OpenTelemetry for instrumentation.** Stack-agnostic SDK; flip the backend without rewriting code. Auto-instrumentation libraries cover most frameworks — opt in before writing manual spans.
3. **Three signals, each answering a different question.**
   - **Logs** — "what happened in this event?" Structured JSON, request-ID correlated.
   - **Metrics** — "how often / how fast?" Cheap, aggregatable, low cardinality.
   - **Traces** — "why is this request slow?" Spans across services.
4. **Cardinality discipline on metrics.** Never label by `user_id` or `task_id` — you create millions of series. Label by enum values (`status=succeeded`, `plan=free/pro`) only. If you need per-user, use logs or traces, not metrics.
5. **Logs: structured, sampled at source.** JSON only. Do not log secrets. Sample successful high-volume requests at 1% in prod; log all errors. Correlate with trace IDs.
6. **Traces: propagate context across service boundaries.** W3C `traceparent` header. Every outgoing HTTP call inherits the trace. Sample by "head" (decide at root) or "tail" (sample based on outcome, e.g. keep all errors).
7. **Golden signals dashboards** per service: rate, errors, duration (Latency p50/p95/p99), saturation. Four graphs tell you if a service is healthy.
8. **Alerts on symptoms, not causes.** Alert on `error rate > 2%` or `p95 latency > 500ms` — user-visible. Do not alert on `CPU > 80%` — that may be fine.

## Example invocation

```python
# Python service — OTel + Grafana Cloud
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

resource = Resource.create({'service.name': 'polly-api', 'service.version': '1.0.0'})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

FastAPIInstrumentor().instrument()

tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

task_created = meter.create_counter('polly.task.created', unit='1', description='Tasks created')

async def create_task(user_id: str, payload: dict):
    with tracer.start_as_current_span('task.create') as span:
        span.set_attribute('task.project_id', payload['project_id'])
        task = await service.create(user_id, payload)
        task_created.add(1, {'project_id': payload['project_id']})
        return task
```

```yaml
# alert rule (Prometheus/Grafana)
- alert: HighErrorRate
  expr: sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.02
  for: 5m
  labels: { severity: critical }
  annotations:
    summary: "Polly API error rate > 2% for 5m"
    runbook: https://handbook.example.com/runbooks/polly-api-errors
```

## Outputs

- An OTel-instrumented service emitting logs, metrics, traces.
- Per-service golden-signals dashboard.
- Alerts on symptoms with runbooks linked.
- Trace context propagated across all service-to-service calls.

## Common failure modes

- Per-user metric labels; cardinality explodes, bills too.
- Logging secrets or full request bodies; security incident plus noise.
- Alerting on causes instead of symptoms; pager fatigue kills the signal.
- No runbook link on alerts; on-call spends 20min figuring out context.
