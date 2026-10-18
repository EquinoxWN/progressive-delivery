#!/usr/bin/env bash
# End-to-end canary on a throwaway kind cluster (Linux with Docker; CI runs it on every push):
# Envoy Gateway and Flagger from pinned charts, the app and its Canary from config/, then a new
# image. Every few seconds the Canary's phase and weight and the HTTPRoute's backend weights are
# recorded; pdcheck judges the recording against the Canary's step schedule.
# Prints ::notice lines (::error on failure) and always deletes the cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}
BIN=.tmp/bin
OUT=.tmp/e2e
CLUSTER=pd-e2e
NEW_IMAGE=ghcr.io/stefanprodan/podinfo:6.15.0@sha256:ec73780a8425f59ea49f5bc8cdff0d598805a224fbaa1f86c67a244f250fa9da
ENVOY_GATEWAY_CHART=v1.9.2
FLAGGER_CHART=1.45.0
mkdir -p "$OUT"
export PATH="$PWD/$BIN:$PATH" KUBECONFIG="$PWD/$OUT/kubeconfig" HELM_CACHE_HOME="$PWD/.tmp/helm/cache" \
  HELM_CONFIG_HOME="$PWD/.tmp/helm/config" HELM_DATA_HOME="$PWD/.tmp/helm/data"

fail() { echo "::error title=canary e2e::$1"; kubectl -n demo describe canary podinfo || true; kubectl -n flagger-system logs deploy/flagger --tail=60 || true; exit 1; }
trap 'kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true' EXIT

bash scripts/tools.sh kind kubectl helm
kind create cluster --name "$CLUSTER" --config config/kind/cluster.yaml --wait 120s

helm install eg oci://docker.io/envoyproxy/gateway-helm --version "$ENVOY_GATEWAY_CHART" \
  -n envoy-gateway-system --create-namespace --wait --timeout 5m
helm repo add flagger https://flagger.app >/dev/null
helm install flagger flagger/flagger --version "$FLAGGER_CHART" -n flagger-system --create-namespace \
  -f config/helm/flagger-values.yaml --wait --timeout 5m

kubectl apply -k config/gateway
kubectl apply -k config/app
kubectl -n demo wait canary/podinfo --for=jsonpath='{.status.phase}'=Initialized --timeout=300s || fail "the Canary did not initialize"
kubectl -n demo rollout status deploy/podinfo-primary --timeout=120s
for svc in podinfo podinfo-primary podinfo-canary; do kubectl -n demo get svc "$svc" >/dev/null || fail "service $svc missing"; done
[ "$(kubectl -n demo get deploy podinfo -o jsonpath='{.spec.replicas}')" = 0 ] || fail "the target deployment was not scaled to zero"
kubectl -n demo get httproute podinfo -o jsonpath='{.spec.rules[0].backendRefs}' > "$OUT/route-initial.json"
echo "::notice title=canary initialized::podinfo-primary ready; services podinfo, podinfo-primary, podinfo-canary; HTTPRoute $(cat "$OUT/route-initial.json")"

# A new image starts the analysis; record until it succeeds or fails (at most 10 minutes).
kubectl -n demo set image deploy/podinfo podinfo="$NEW_IMAGE"
: > "$OUT/observed.jsonl"
start=$(date +%s)
while :; do
  now=$(( $(date +%s) - start ))
  phase=$(kubectl -n demo get canary podinfo -o jsonpath='{.status.phase}')
  weight=$(kubectl -n demo get canary podinfo -o jsonpath='{.status.canaryWeight}')
  primary=$(kubectl -n demo get httproute podinfo -o jsonpath='{.spec.rules[0].backendRefs[?(@.name=="podinfo-primary")].weight}')
  canary=$(kubectl -n demo get httproute podinfo -o jsonpath='{.spec.rules[0].backendRefs[?(@.name=="podinfo-canary")].weight}')
  echo "{\"t\": $now, \"phase\": \"$phase\", \"weight\": ${weight:-0}, \"primary\": ${primary:-0}, \"canary\": ${canary:-0}}" | tee -a "$OUT/observed.jsonl"
  if [ "$now" -gt 30 ] && { [ "$phase" = Succeeded ] || [ "$phase" = Failed ]; }; then break; fi
  [ "$now" -lt 600 ] || fail "no promotion within 10 minutes"
  sleep 2
done

$PY -m pdcheck judge "$OUT/observed.jsonl" | tee "$OUT/judge.json" || fail "the rollout did not follow the schedule: $(cat "$OUT/judge.json")"
promoted=$(kubectl -n demo get deploy podinfo-primary -o jsonpath='{.spec.template.spec.containers[0].image}')
[ "$promoted" = "$NEW_IMAGE" ] || fail "podinfo-primary runs $promoted, not the new image"
echo "::notice title=canary promoted::$(cat "$OUT/judge.json"); primary now runs podinfo 6.15.0"
echo "E2E PASS"
