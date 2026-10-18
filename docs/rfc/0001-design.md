# RFC 0001: progressive-delivery design

- **Status:** Accepted for M1
- **Author:** EquinoxWN
- **Created:** 2026-10-18

## Problem

A Kubernetes rolling update replaces pods by readiness alone: if the new version starts and answers
its health check, every user gets it within minutes, even when it returns errors for real
requests. Progressive delivery sends a small, growing share of traffic to the new version first,
so a bad release reaches a few percent of users and can be stopped. Flagger automates that, but its
configuration (the Canary resource, the traffic provider, the step schedule) is easy to get wrong in
ways that only show up during a release.

M1 sets up the mechanism: a Canary resource for which Flagger creates the primary deployment and
routes traffic between primary and canary, and a schedule that shifts traffic in steps (5%, 10%,
20% and so on) when a new image appears; the configuration is checked offline on every change,
and a real rollout runs in CI.

**Users:** platform and release engineers who want automatic, gradual releases on Kubernetes, and
people learning how canary analysis works.

## Define: critical-to-quality measures (CTQs)

| CTQ | Target | Measured by |
|---|---|---|
| Canary is valid | 0 violations of Flagger v1.45.0's Canary schema (pinned CRD) | `pdcheck validate` |
| Objects are valid | Kubernetes 1.36 and CRD schemas: all objects valid | kubeconform |
| References hold | the Canary's target Deployment, container port and Gateway exist, and the Gateway admits routes from the app's namespace | `pdcheck validate` |
| Workload follows house rules | images pinned by digest, non-root, read-only root, no privilege escalation, capabilities dropped, probes, requests and limits, no host access | `pdcheck validate` |
| Checks catch mistakes | every one of 24 deliberate mistakes is reported | mutation tests |
| Primary created and routed | after apply: Canary `Initialized`, `podinfo-primary` ready, target scaled to 0, three services, HTTPRoute 100/0 | `scripts/e2e.sh` (CI) |
| Steps followed | after a new image: canary weights 5, 10, 20, 40, 60, 80 in order, route weights summing to 100, then `Succeeded` with the new image on the primary | `scripts/e2e.sh` + `pdcheck judge` (CI) |

## Goals (M1)

- You define a Canary resource; Flagger creates a primary deployment and routes traffic between
  primary and canary.
- When a new image appears, traffic shifts in steps (5%, 10%, 20% and so on).

## Non-goals

- Prometheus success-rate and latency checks, and automatic rollback on bad metrics (M2): M1's
  analysis has no metrics, so every step passes on time.
- Manual approval gates, the good-versus-broken release demo and its recordings (M3).
- Running a cluster on the author's laptop: no Docker is used locally; the rollout runs in CI.

## Design

```
config/app      Namespace demo (restricted Pod Security), Deployment podinfo (digest-pinned),
                Canary podinfo: gatewayRefs gateway/public, interval 15s, stepWeights 5..80
config/gateway  GatewayClass eg (Envoy Gateway), Gateway public (HTTP :80, routes from demo only)
config/helm     Flagger chart values: meshProvider gatewayapi:v1, no bundled Prometheus
config/kind     one-node cluster, node image pinned by digest

pdcheck validate   Canary vs Flagger v1.45.0 CRD schema (downloaded, SHA-256 pinned), house rules,
                   cross-references, schedule sanity
kubeconform        Kubernetes 1.36.4 schemas + CRD catalog for Canary, Gateway, GatewayClass
scripts/e2e.sh     kind -> Envoy Gateway v1.9.2 -> Flagger 1.45.0 -> apply -> wait Initialized ->
                   set image 6.15.0 -> sample phase, weight, HTTPRoute weights every 2 s ->
                   pdcheck judge -> primary runs 6.15.0 -> delete cluster
```

Flagger, given the Canary, copies the Deployment to `podinfo-primary`, creates the services
`podinfo`, `podinfo-primary` and `podinfo-canary`, scales the original to zero and writes an
HTTPRoute on the shared Gateway with backend weights primary 100, canary 0. A changed pod
template starts an analysis: Flagger scales the canary up and moves the HTTPRoute weights one
step per interval, then copies the new spec to the primary, routes everything back to it and
scales the canary down.

## Alternatives considered

| Option | Why not (yet) |
|---|---|
| Argo Rollouts | Replaces the Deployment with its own Rollout kind; Flagger works on plain Deployments and was the backlog's choice. |
| NGINX ingress as the traffic provider | The Kubernetes ingress-nginx project was retired in 2026; the Gateway API is the supported routing API (ADR 0002). |
| A service mesh (Istio, Linkerd) | Adds sidecars or a data plane to every pod; only north-south traffic needs shifting here. |
| `stepWeight` + `maxWeight` (linear steps) | Linear 10% steps send 10% of users to a new version in the first interval; explicit `stepWeights` start at 5% and double, as the backlog asks. |
| Testing on a local cluster | Needs Docker; the owner's machine shares one Docker VM between sessions, so the cluster runs in CI and locally only offline checks run. |

## Measurement plan

- On every change: yamllint, ruff, mypy, `pdcheck validate`, kubeconform and the mutation tests.
- In CI: the kind rollout, its recorded samples judged by `pdcheck judge`, notices for each stage.
- Results, root causes and controls in `docs/results/m1.md`.

## Milestones

- **M1:** You define a Canary resource; Flagger creates a primary deployment and routes traffic between primary and canary. When a new image appears, traffic shifts in steps (5%, 10%, 20% and so on).
- **M2:** At each step Flagger queries Prometheus for success rate and p99 latency against thresholds. Healthy metrics keep the rollout moving until promotion; unhealthy ones roll back to the primary automatically.
- **M3:** A webhook gate can require manual approval before production. The demo ships one good release and one with injected errors, and records both.

## Risks and open questions

- Without metrics (M1) a canary never fails analysis: a broken release would be promoted on
  schedule. M2's checks are what make the steps protective.
- The CRD catalog kubeconform uses is community-maintained and not pinned; the pinned Flagger CRD
  in `pdcheck` is the authoritative schema check.
- The end-to-end run has not been executed by the author yet (no local Docker); its first CI run
  is the first real rollout.
