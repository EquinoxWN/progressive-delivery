# ADR 0002: Shift traffic through the Gateway API, served by Envoy Gateway

- **Status:** Accepted

## Context

Flagger moves traffic by rewriting a routing object of a provider it supports: a service mesh, an
ingress controller, or the Gateway API. The scaffold named "Gateway API or NGINX ingress". The
Kubernetes ingress-nginx project was retired in 2026, so building new configuration on it would
start on an unmaintained component. The Gateway API is the Kubernetes routing API that replaces
Ingress, and Flagger supports it through HTTPRoute backend weights.

## Decision

Use Flagger's `gatewayapi:v1` provider with Envoy Gateway (chart v1.9.2) as the implementation:
one shared `Gateway` in its own namespace whose listener admits routes only from namespaces
labelled `demo`, and a Canary that references it with `gatewayRefs`. Flagger writes and owns the
HTTPRoute; the end-to-end run reads its backend weights directly to see the traffic split.

## Consequences

- The traffic split is visible as plain Kubernetes data (`HTTPRoute.spec.rules[].backendRefs[].weight`),
  which the end-to-end run records next to the Canary's own status.
- Portable: another Gateway API implementation can replace Envoy Gateway by changing the
  GatewayClass, without touching the Canary.
- Two more moving parts in the cluster (Envoy Gateway controller and proxy) than an ingress
  controller alone; the CI cluster has to start them before Flagger.
- Cross-namespace routing is explicit: if the app moves namespace without its label, the Gateway
  refuses its route, and `pdcheck validate` reports it before deployment.
