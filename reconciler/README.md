# reconciler

Idempotent REST reconciler for the `mega-media` Helm chart. Replaces the
old `sync-prowlarr-hook.yaml` Job that wrote directly to the Prowlarr
Postgres tables (brittle across *arr version bumps).

Runs as a CronJob inside the cluster, reads a YAML config rendered from
`values.yaml` into a ConfigMap, and converges:

- Prowlarr `Applications` (Sonarr/Radarr/Lidarr/Readarr registrations)
- Prowlarr `Indexers` (from `arrs.prowlarr.indexers[]`)
- Prowlarr `DownloadClients` (SABnzbd, if enabled)
- Each *arr's `DownloadClients` (SABnzbd, if enabled)

Each resource is keyed by `name`; the reconciler GETs the list, finds the
matching row, and PUTs/POSTs as needed. Safe to re-run.

Build:

```sh
docker build -t ghcr.io/<owner>/mega-media-reconciler:<tag> reconciler/
docker push  ghcr.io/<owner>/mega-media-reconciler:<tag>
```

Run locally for testing (against a real cluster's exposed services):

```sh
export SONARR_API_KEY=...
# ...
LOG_LEVEL=DEBUG python reconciler.py
```

## Out of scope (intentional)

- Root folders. Each *arr's root folder is currently set up via the UI on
  first run; can be added here later but Lidarr/Readarr require a quality
  profile id that isn't known until the *arr boots.
- Quality profiles, custom formats, naming. Use Recyclarr/Profilarr for
  those — they're the right tool, and this reconciler doesn't try to be.
- Removing unmanaged resources. Reconciler is additive only: anything the
  user creates via the UI is left alone.
