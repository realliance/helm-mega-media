# reconciler

Idempotent REST reconciler for the `mega-media` Helm chart. Replaces the
old `sync-prowlarr-hook.yaml` Job that wrote directly to the Prowlarr
Postgres tables (brittle across *arr version bumps).

Runs as a CronJob inside the cluster, reads a YAML config rendered from
`values.yaml` into a ConfigMap, and converges:

- Prowlarr `Applications` (Sonarr/Radarr/Lidarr/Readarr registrations,
  including the `arrs.<svc>.search.*` block — syncCategories, anime
  sync flags, blocklisted-hash rejection)
- Prowlarr `Indexers` (from `arrs.prowlarr.indexers[]`)
- Prowlarr `DownloadClients` (SABnzbd, if enabled)
- Each *arr's `DownloadClients` (SABnzbd, if enabled)
- Each *arr's `RootFolders` (from `arrs.<svc>.mediaDir` →
  `/media/<mediaDir>`). Lidarr/Readarr resolve their required
  `defaultQualityProfileId` and `defaultMetadataProfileId` against the
  live *arr at run time; Readarr additionally gets
  `isCalibreLibrary: false`.

Each resource is keyed by `name` (or `path` for RootFolders); the
reconciler GETs the list, finds the matching row, and PUTs/POSTs as
needed. Safe to re-run.

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

- Quality profiles, custom formats, naming. Use Recyclarr/Profilarr for
  those — they're the right tool, and this reconciler doesn't try to be.
- Removing unmanaged resources. Reconciler is additive only: anything the
  user creates via the UI is left alone. A strict mode that prunes
  unmanaged rows is plausible but blast-radius-sensitive, deferred to v3.
- Per-category download-client routing. Prowlarr can route different
  categories to different download clients per *arr; the reconciler
  currently pushes a single SABnzbd download client everywhere.
