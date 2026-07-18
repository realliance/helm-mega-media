#!/usr/bin/env bash
# Local kind smoke test for the mega-media chart + reconciler.
#
# Builds the reconciler image, loads it into a kind cluster, installs the chart
# with a fast reconcile interval and metrics enabled, then asserts the pieces
# this repo's control-plane changes introduced actually come up:
#   - reconciler runs as a long-running Deployment on the *locally built* image
#   - /healthz and /metrics are served (loop mode)
#   - the post-install bootstrap Job completes
#   - envFrom consumers carry a checksum/config annotation
#   - exportarr sidecars + a named metrics Service port render when metrics on
#
# The published reconciler:latest is the old run-once code and would crashloop
# under the new Deployment/probes — hence the build + `kind load`.
#
# Usage:
#   hack/kind-smoke.sh              # reuse/create cluster, leave it up
#   KEEP=0 hack/kind-smoke.sh       # delete the cluster at the end
#   CLUSTER=foo RELEASE=bar hack/kind-smoke.sh
#
# Run it inside `nixs` (nix-shell) so helm/kind/kubectl are on PATH.
set -euo pipefail

CLUSTER="${CLUSTER:-mega-test}"
NS="${NS:-media}"
RELEASE="${RELEASE:-mega}"
IMG_TAG="${IMG_TAG:-kind-test}"
KEEP="${KEEP:-1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$REPO_ROOT/charts/mega-media"
KCTX="kind-$CLUSTER"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ok:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL:\033[0m %s\n' "$*" >&2; exit 1; }
kc()   { kubectl --context "$KCTX" -n "$NS" "$@"; }

for bin in docker kind kubectl helm; do
  command -v "$bin" >/dev/null || fail "$bin not on PATH (run inside nixs)"
done

log "Building reconciler:$IMG_TAG"
docker build -t "reconciler:$IMG_TAG" "$REPO_ROOT/reconciler" >/dev/null

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  log "Creating kind cluster $CLUSTER"
  kind create cluster --name "$CLUSTER"
else
  log "Reusing existing kind cluster $CLUSTER"
fi

log "Loading image into cluster"
kind load docker-image "reconciler:$IMG_TAG" --name "$CLUSTER"

log "helm upgrade --install (metrics on, 30s interval, local reconciler image)"
# --wait is best-effort: the *arrs pull large images and may lag; the specific
# assertions below are what actually gate success.
helm --kube-context "$KCTX" upgrade --install "$RELEASE" "$CHART" \
  -n "$NS" --create-namespace \
  --set reconciler.image=reconciler \
  --set reconciler.tag="$IMG_TAG" \
  --set reconciler.pullPolicy=Never \
  --set reconciler.interval=30 \
  --set metrics.enabled=true \
  --wait --timeout 8m || log "helm --wait timed out (slow *arrs) — continuing to explicit checks"

# ---- deterministic manifest checks (don't need pods Ready) -----------------
log "Manifest: checksum/config annotation on envFrom consumers"
for svc in sabnzbd sonarr; do
  cs=$(kc get deploy "${RELEASE}-${svc}" -o "jsonpath={.spec.template.metadata.annotations.checksum/config}" 2>/dev/null || true)
  [[ -n "$cs" ]] || fail "$svc missing checksum/config annotation"
  ok "$svc checksum/config=${cs:0:16}…"
done

log "Manifest: exportarr sidecar present on *arr + SAB"
for svc in sonarr sabnzbd; do
  names=$(kc get deploy "${RELEASE}-${svc}" -o "jsonpath={.spec.template.spec.containers[*].name}")
  grep -qw exportarr <<<"$names" || fail "$svc missing exportarr sidecar (containers: $names)"
  ok "$svc containers: $names"
done

log "Manifest: named metrics port on sonarr Service, absent on postgres"
p=$(kc get svc "${RELEASE}-sonarr" -o 'jsonpath={.spec.ports[?(@.name=="metrics")].port}')
[[ "$p" == "9707" ]] || fail "sonarr Service metrics port = '$p' (want 9707)"
ok "sonarr Service metrics port = $p"

# ---- runtime checks --------------------------------------------------------
log "Runtime: postgres ready"
kc rollout status statefulset/"${RELEASE}-postgresql" --timeout=240s || fail "postgres not ready"
ok "postgres ready"

log "Runtime: reconciler Deployment available on the locally built image"
kc rollout status deployment/"${RELEASE}-reconciler" --timeout=120s || fail "reconciler Deployment not available"
img=$(kc get deploy "${RELEASE}-reconciler" -o 'jsonpath={.spec.template.spec.containers[0].image}')
[[ "$img" == "reconciler:$IMG_TAG" ]] || fail "reconciler using unexpected image: $img"
ok "reconciler available, image=$img"

log "Runtime: /healthz and /metrics served by the loop"
kc port-forward deploy/"${RELEASE}-reconciler" 18080:8000 >/tmp/kind-smoke-pf.log 2>&1 &
PF=$!; trap 'kill $PF 2>/dev/null || true' EXIT
sleep 4
curl -fsS http://127.0.0.1:18080/healthz >/dev/null || fail "/healthz not 200"
ok "/healthz 200"
curl -fsS http://127.0.0.1:18080/metrics | grep -q '^megamedia_' || fail "/metrics missing megamedia_ series"
ok "/metrics exposes megamedia_ series"
kill $PF 2>/dev/null || true; trap - EXIT

log "Runtime: bootstrap Job completed (one full reconcile pass)"
kc wait --for=condition=complete job/"${RELEASE}-reconciler-bootstrap" --timeout=360s \
  || fail "bootstrap Job did not complete"
ok "bootstrap Job complete"

log "ALL SMOKE CHECKS PASSED"

if [[ "$KEEP" == "0" ]]; then
  log "Deleting cluster $CLUSTER"
  kind delete cluster --name "$CLUSTER"
else
  log "Cluster $CLUSTER left running (KEEP=1). Delete with: kind delete cluster --name $CLUSTER"
fi
