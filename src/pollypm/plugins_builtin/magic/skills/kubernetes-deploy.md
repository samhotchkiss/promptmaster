---
name: kubernetes-deploy
description: Container deploys on Kubernetes — manifests, Helm, probes, observability, and safe rollouts.
when_to_trigger:
  - kubernetes
  - k8s deploy
  - helm chart
  - kubectl
kind: magic_skill
attribution: https://github.com/kubernetes/kubernetes
---

# Kubernetes Deploy

## When to use

Use when the target is Kubernetes — managed (EKS/GKE/AKS) or self-hosted. Do not reach for it for a simple web app; Vercel, Fly, or Railway are faster and cheaper below medium scale. Kubernetes is the right answer when you have many services, complex networking, or you are already running it for other workloads.

## Process

1. **Manifests or Helm, pick one.** Raw manifests for 1-2 services, Helm (or Kustomize) when variants multiply across environments. Do not hand-edit manifests in each environment; template them.
2. **Deployment + Service is the baseline.** Deployment manages pods (replicas, rolling update strategy); Service gives them a stable DNS name. HorizontalPodAutoscaler for dynamic scaling.
3. **Liveness, readiness, startup probes** on every container. Liveness = "is it alive?" — failure restarts. Readiness = "can it take traffic?" — failure removes from Service. Startup = "has it booted?" — for slow-start apps, prevents early liveness kills.
4. **Requests and limits, always.** `resources.requests.cpu/memory` for scheduling. `resources.limits.cpu/memory` to cap. Requests without limits lets one bad pod starve a node; limits without requests causes bad scheduling.
5. **Rolling updates with maxSurge/maxUnavailable.** `strategy.rollingUpdate: { maxSurge: 25%, maxUnavailable: 0 }` for zero-downtime. For databases, `Recreate` strategy (brief downtime) is safer than rolling.
6. **Secrets in a secrets manager**, not Kubernetes Secrets. External Secrets Operator pulls from AWS Secrets Manager / GCP Secret Manager / Vault and syncs into Kubernetes at runtime. Do not commit secrets manifests even with `sealed-secrets`.
7. **Network policies denying by default.** Default all-allow is a misconfiguration waiting to happen. `NetworkPolicy` that explicitly allows only the traffic each service needs.
8. **Observability via Prometheus + Loki + Grafana** or a managed stack (Datadog, Honeycomb). Every service exposes `/metrics` in Prometheus format; ServiceMonitor CR wires scraping.

## Example invocation

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: polly-api
  labels: { app: polly-api }
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 25%, maxUnavailable: 0 }
  selector:
    matchLabels: { app: polly-api }
  template:
    metadata:
      labels: { app: polly-api }
    spec:
      containers:
        - name: api
          image: registry.example.com/polly-api:v1.0.0   # immutable tag, never :latest
          ports: [{ containerPort: 8080 }]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: { name: polly-secrets, key: database-url }
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits:   { cpu: 1000m, memory: 512Mi }
          readinessProbe:
            httpGet: { path: /readyz, port: 8080 }
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 10
            failureThreshold: 3
          startupProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 5
            failureThreshold: 30
---
apiVersion: v1
kind: Service
metadata: { name: polly-api }
spec:
  selector: { app: polly-api }
  ports: [{ port: 80, targetPort: 8080 }]
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: polly-api }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: polly-api }
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource: { name: cpu, target: { type: Utilization, averageUtilization: 70 } }
```

## Outputs

- `deployment.yaml`, `service.yaml`, `hpa.yaml` (or a Helm chart).
- All probes configured.
- Requests + limits set.
- Secrets pulled from an external manager.
- Default-deny NetworkPolicies + explicit allows.

## Common failure modes

- `:latest` image tags; rollback impossible, "last known good" drifts.
- No readiness probe; traffic hits pods before they can handle it.
- No limits; one bad pod saturates a node.
- Kubernetes Secrets committed to git — even sealed ones leak through compromise.
