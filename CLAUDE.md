# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

Single Helm chart (`mega-media`) that deploys an entire homelab media stack (Sonarr/Radarr/Lidarr/Readarr/Prowlarr + SABnzbd + Jellyfin/Plex) onto **one node**. The single-node assumption is deliberate: media storage uses `ReadWriteOnce`, so every workload must co-schedule via `podAffinity` (see `mega-media.sameNodePodAffinity` in `_helpers.tpl`). Do not introduce templates that assume multi-node scheduling.

## Dev Environment

```sh
nixs   # enters shell.nix — provides helm, kubectl, kustomize, kind, jq, pre-commit
```

Local cluster (rootless docker on NixOS doesn't play well with minikube's docker driver — use kind):

```sh
kind create cluster --name mega-test
helm upgrade --install mega charts/mega-media -n media --create-namespace
kind delete cluster --name mega-test   # when done
```

## Common Commands

```sh
# Render templates locally (run from charts/mega-media)
helm template test . > /tmp/out.yaml

# Lint
helm lint charts/mega-media

# Install/upgrade against a live cluster
helm upgrade --install mega charts/mega-media -n media --create-namespace

# Reconciler (separate Python app under reconciler/) — tests
cd reconciler && pip install -r requirements-dev.txt && python -m pytest -v
python -m pytest -v -k <name>   # single test
```

There is no `helm dependency update` step and no `charts/mega-media/charts/` subdirectory — Postgres is an inline StatefulSet, not a vendored subchart (see below).

## Release

Two independent CI pipelines, both triggered by pushes to `main`:

- `.github/workflows/publish.yml` → `helm/chart-releaser-action` with `skip_existing: true`. **Bump `charts/mega-media/Chart.yaml`'s `version:` for any chart/template change** or the release step silently no-ops.
- `.github/workflows/reconciler-image.yml` — path-filtered to `reconciler/**`. Runs pytest on every PR, and on `main` pushes / manual dispatch builds and pushes `ghcr.io/<repo>/reconciler:<sha>` + `:latest`. Editing only `reconciler/` does **not** require a chart version bump; editing chart templates does.

## Architecture

### Template entry points vs. helpers

Templates in `charts/mega-media/templates/*.yaml` are the rendered manifests. Reusable logic lives in `templates/helpers/_*.tpl` and is invoked with `template` / `include`. The pattern repeated everywhere:

```
{{- $contextWithName := merge (dict "name" "<service>") $ -}}
{{ template "mega-media.<helper>" $contextWithName }}
```

Helpers expect `.name` to be set on the context — that's how `mega-media.name` produces `<release>-<service>` and how `mega-media.selectorLabels` differentiates workloads via the `media/service` label. When adding a new template, follow the `merge (dict "name" ...) $` pattern rather than relying on `.Values.<svc>` directly.

### *arr services are generated, not duplicated

`templates/arr-deployments.yaml` ranges over `tuple "sonarr" "radarr" "lidarr" "readarr" "prowlarr"` and calls `mega-media.arr.deployment` (in `_arr_deployment.tpl`) for each enabled service. To add a new *arr-like service, extend that tuple **and** add a matching block under `arrs:` in `values.yaml`. Jellyfin/Plex/SABnzbd have their own per-service template files because they don't share the *arr init/config shape.

### Postgres is mandatory; inline StatefulSet or external

Every *arr is configured to use Postgres (see the `<Config>` XML written by the `init-myservice` initContainer in `_arr_deployment.tpl`). When `postgresql.enabled` (the default), the chart renders its **own** StatefulSet + Service + Secret (`templates/postgres-statefulset.yaml`, `-service.yaml`, `-secret.yaml`) — there is no Bitnami subchart anymore (removed in `c9b2f37`). Despite that, the Service name is still `<release>-postgresql`, so the host ternary is unchanged:

```
{{- $db_host := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.host -}}
```

This ternary now lives only in `_arr_deployment.tpl`. If you add a new DB-using workload, replicate it — there's no shared helper for it yet. Each *arr pre-creates three databases via `mega-media.initDb`: `<name>_main`, `<name>_log`, `<name>_cache`.

### API key handling (subtle, easy to break)

Each *arr and SABnzbd needs an API key. The pattern in `_arr_deployment.tpl` / `sabnzbd-deployment.yaml`:

- If the user set `<svc>.apiKey: {name, key}` in values, reference that **external** secret.
- Otherwise, generate one via `randAlphaNum` and create a `<svc>-api-key` Secret via `mega-media.api-key-secret`.

The external-secret branch exists specifically because Argo CD doesn't distinguish install from upgrade and would otherwise rotate generated keys on every sync (see commit `302ec29`). When editing API-key flow, preserve both branches and keep the `hasKey ... | ternary` pattern.

### Cross-wiring is the REST reconciler (was: a SQL hook)

The old `sync-prowlarr-hook.yaml` Job that `psql`'d directly into `prowlarr_main` is **gone** (replaced in `8e0c258`). Cross-wiring is now a standalone Python app under `reconciler/` (`reconciler.py` + pytest `test_reconciler.py`, its own `Dockerfile`, published to `ghcr.io/<repo>/reconciler`). It talks to the documented *arr/Prowlarr **REST APIs**, which are stable across major versions where the DB schema is not. It converges:

- Prowlarr `Applications` (the other *arrs, including each `arrs.<svc>.search` block — syncCategories etc.), `Indexers` (from `arrs.prowlarr.indexers[]`), and `DownloadClients` (SABnzbd)
- Each *arr's `DownloadClients` (SABnzbd) and `RootFolders` (`/media/<mediaDir>`, or `arrs.<svc>.rootFolderPath`)

Design invariants to preserve when editing it:

- **Additive/idempotent only.** Each resource is keyed by `name` (or `path` for RootFolders); GET the list, match, PUT/POST. Never prunes UI-created resources. Safe to re-run.
- **Long-running loop by default.** `main()` runs `reconcile_once()` on a `RECONCILE_INTERVAL` loop (default 300s), reloading `/etc/reconciler/config.yaml` each pass (it's a volume mount, so config-map edits propagate with no restart), handling SIGTERM for a clean shutdown, and serving `/healthz`, `/readyz`, `/metrics` (prometheus, `megamedia_*`) on `METRICS_PORT`. Set `RECONCILE_ONESHOT=1` for a single pass (the bootstrap Job and CronJob mode use this).
- **Soft-fail per resource.** `reconcile_once()` catches per-*arr and per-resource errors and returns a failures list; `main()`'s loop also wraps each pass in a catch-all so a transient error never kills the process (see `c88e14d`, `fed3f67`, `a680168`). `wait_for_ready` polls `/ping` and *skips* services that never come up rather than aborting.
- **Version preflight (advisory).** `check_version()` logs each *arr's `/system/status` version every pass and warns if below `arrs.<svc>.minVersion` (a known-good floor). Never gates the reconcile — the REST *contract* is the stability bet.
- **API versions differ:** sonarr/radarr are `v3`, lidarr/readarr/prowlarr are `v1` (`ARR_API_VERSIONS`). Lidarr/Readarr resolve `defaultQualityProfileId`/`defaultMetadataProfileId` against the live *arr at run time.
- Out of scope by design: quality profiles/custom formats (use Recyclarr/Profilarr) and pruning unmanaged rows.

**Chart ↔ reconciler wiring** (three templates, all gated on `reconciler.enabled`):

- `reconciler-workload.yaml` — renders **either** a long-running **Deployment** (`reconciler.mode=deployment`, the default; probes on `/healthz`+`/readyz`, exposes `metricsPort`) **or** a legacy **CronJob** (`reconciler.mode=cronjob`, `schedule` from values), **plus** a `post-install,post-upgrade` bootstrap Job in both modes. All share the pod spec; the Job/CronJob call sites pass `oneshot: true`.
- `_reconciler.tpl` (`mega-media.reconciler.podspec`) — the shared pod spec. `oneshot` toggles restartPolicy (Always vs OnFailure), `RECONCILE_ONESHOT` vs `RECONCILE_INTERVAL`/`METRICS_PORT`, and whether the metrics port + probes render. Injects API keys as env vars: `ARR_<NAME>_API_KEY` per enabled *arr, `SAB_API_KEY`, `INDEXER_<i>_API_KEY`. It reuses the same `hasKey ... apiKey | ternary` external-vs-generated secret logic as the deployments — keep it in sync with the API-key section above.
- `reconciler-config.yaml` — renders `values.yaml` (arr URLs/ports/search/`minVersion`, sabnzbd, indexers) into a ConfigMap mounted at `/etc/reconciler/config.yaml`. The reconciler reads config from the file and secrets from env by the `apiKeyEnv` names this template emits — the two must agree.

Changes to *arr names, ports, or service-DNS shape ripple through `reconciler-config.yaml` and the Python; re-render and re-run pytest when touching those.

### Service DNS shape

URLs are constructed inline as `http://<mega-media.name>.<Release.Namespace>.svc.cluster.local:<port>` (see `reconciler-config.yaml`). There's no central URL helper — grep for `svc.cluster.local` before renaming anything. Note the SABnzbd download-client `host` is a **bare hostname** (no scheme/port) — *arr APIs reject a scheme prefix there (`44e7fe4`).

## Conventions

- `values.yaml` is the contract; nearly every behavior is values-driven. Default-disable new optional workloads (`enabled: false`).
- Per-workload `enabled` flags gate the entire template block — wrap new resources in `{{- if .enabled -}}`.
- Use `mega-media.labels` / `mega-media.selectorLabels` for every new resource so service selectors keep matching.
- The `media` PVC (`templates/media-pvc.yaml`) is the single shared volume; mount it as `/media` and write into a per-service `mediaDir` subpath (see the `init-media-subpath` initContainer pattern).
- **Config reload:** consumers that read a ConfigMap via `envFrom` (SAB + *arrs read `arr-config`) never live-update, so their pod templates carry a `checksum/config` annotation (`sha256sum` of the rendered `arr-config.yaml`) that rolls them on change. The reconciler doesn't need this — it re-reads its mounted config each loop pass.
- **Metrics** (`metrics.enabled`, default off): adds an `exportarr` sidecar to each *arr (`_arr_deployment.tpl`) and SAB (`sabnzbd-deployment.yaml`), reusing the app's own API-key secret, and a named `metrics` port on those Services via `_service.tpl` (gated by a `metricsSidecar` flag the *arr/SAB service calls pass — jellyfin/plex/postgres don't). No ServiceMonitor is rendered (luma-homeops owns monitors). SAB's exportarr URL must include the `/sabnzbd` url_base.
