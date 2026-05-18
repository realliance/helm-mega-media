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

# Dependency refresh (Bitnami postgresql is vendored under charts/mega-media/charts/)
helm dependency update charts/mega-media

# Install/upgrade against a live cluster
helm upgrade --install mega charts/mega-media -n media --create-namespace
```

## Release

Pushing to `main` triggers `.github/workflows/publish.yml` → `helm/chart-releaser-action` with `skip_existing: true`. **Bump `charts/mega-media/Chart.yaml`'s `version:` for any template change** or the release step silently no-ops.

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

### Postgres is mandatory; bundled or external

Every *arr is configured to use Postgres (see the `<Config>` XML written by the `init-myservice` initContainer in `_arr_deployment.tpl`). The DB host/port/user/secret are resolved with this ternary, repeated in every template that touches the DB:

```
{{- $db_host := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.host -}}
```

If you add a new DB-using workload, replicate this block — there's no shared helper for it yet. Each *arr pre-creates three databases via `mega-media.initDb`: `<name>_main`, `<name>_log`, `<name>_cache`.

### API key handling (subtle, easy to break)

Each *arr and SABnzbd needs an API key. The pattern in `_arr_deployment.tpl` / `sabnzbd-deployment.yaml`:

- If the user set `<svc>.apiKey: {name, key}` in values, reference that **external** secret.
- Otherwise, generate one via `randAlphaNum` and create a `<svc>-api-key` Secret via `mega-media.api-key-secret`.

The external-secret branch exists specifically because Argo CD doesn't distinguish install from upgrade and would otherwise rotate generated keys on every sync (see commit `302ec29`). When editing API-key flow, preserve both branches and keep the `hasKey ... | ternary` pattern.

### Prowlarr cross-wiring is a post-install Job

`templates/sync-prowlarr-hook.yaml` runs as a `helm.sh/hook: post-install` Job and uses `_prowlarr_insert_apps.tpl` to `psql` directly into the `prowlarr_main` DB, populating the `Applications`, `Indexers`, and `DownloadClients` tables so Prowlarr already knows about the other *arrs and SABnzbd on first boot. This bypasses Prowlarr's REST API entirely. Changes to *arr names, ports, or service-DNS shape will break this Job — re-render and inspect the SQL when touching those.

### Service DNS shape

URLs are constructed inline as `http://<mega-media.name>.<Release.Namespace>.svc.cluster.local:<port>` (see `_prowlarr_insert_apps.tpl`). There's no central URL helper — grep for `svc.cluster.local` before renaming anything.

## Conventions

- `values.yaml` is the contract; nearly every behavior is values-driven. Default-disable new optional workloads (`enabled: false`).
- Per-workload `enabled` flags gate the entire template block — wrap new resources in `{{- if .enabled -}}`.
- Use `mega-media.labels` / `mega-media.selectorLabels` for every new resource so service selectors keep matching.
- The `media` PVC (`templates/media-pvc.yaml`) is the single shared volume; mount it as `/media` and write into a per-service `mediaDir` subpath (see the `init-media-subpath` initContainer pattern).
