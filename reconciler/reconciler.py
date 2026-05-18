"""Idempotent REST reconciler for the mega-media stack.

Reads /etc/reconciler/config.yaml (from a ConfigMap) plus API keys mounted
as env vars, then converges Prowlarr's Applications/Indexers/DownloadClients
plus each *arr's DownloadClient and RootFolder against the desired state.
Safe to re-run on a CronJob schedule; each resource is keyed and PUT in
place rather than recreated.

Bypasses raw DB inserts (the chart's previous approach) — talks to the
documented REST APIs, which are stable across major *arr versions where
the DB schema is not.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

log = logging.getLogger("reconciler")


@dataclass
class ArrService:
    name: str                                 # "sonarr"
    impl: str                                 # "Sonarr" (Prowlarr's Implementation/ConfigContract prefix)
    url: str                                  # http://mega-sonarr.media.svc.cluster.local:8989
    api_key: str
    api_version: str                          # "v3" for sonarr/radarr, "v1" for lidarr/readarr/prowlarr
    root_folder_path: str | None = None       # resolved by helm: defaults to /media/<mediaDir>, overridable via arrs.<svc>.rootFolderPath
    search: dict[str, Any] = field(default_factory=dict)  # syncCategories etc., merged into Prowlarr Application fields


# Lidarr and Readarr are still on v1; Sonarr and Radarr are on v3.
ARR_API_VERSIONS = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
    "readarr": "v1",
}


def load_config() -> dict[str, Any]:
    with open("/etc/reconciler/config.yaml") as f:
        return yaml.safe_load(f)


def env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


def wait_for_ready(services: list[ArrService], timeout: int = 300) -> list[ArrService]:
    """Poll each /ping until 200 or timeout, returning the services that became
    ready. *arr instances refuse POSTs while their DB migrations are still
    running, so this avoids racing a fresh install. Unreachable services are
    skipped with a warning rather than aborting the reconcile — one bad *arr
    (broken image, crash loop) shouldn't starve the others."""
    deadline = time.time() + timeout
    pending = list(services)
    ready: list[ArrService] = []
    while pending and time.time() < deadline:
        still_pending: list[ArrService] = []
        for svc in pending:
            try:
                r = httpx.get(f"{svc.url}/ping", timeout=5)
                if r.status_code == 200:
                    log.info("ready: %s", svc.name)
                    ready.append(svc)
                    continue
            except httpx.RequestError as e:
                log.debug("not ready %s: %s", svc.name, e)
            still_pending.append(svc)
        if still_pending:
            time.sleep(3)
        pending = still_pending
    if pending:
        names = ", ".join(s.name for s in pending)
        log.warning("services did not become ready (skipping): %s", names)
    return ready


def arr_client(svc: ArrService) -> httpx.Client:
    return httpx.Client(
        base_url=f"{svc.url}/api/{svc.api_version}",
        headers={"X-Api-Key": svc.api_key},
        timeout=30,
    )


def upsert(client: httpx.Client, resource: str, desired: dict[str, Any], key: str = "name") -> None:
    """GET /api/<ver>/<resource>, find by `key` (default "name"), PUT if present,
    POST if not. Use key="path" for RootFolders, whose natural identity is the
    filesystem path rather than a display name."""
    identity = desired.get(key)
    existing = client.get(f"/{resource}").raise_for_status().json()
    match = next((r for r in existing if r.get(key) == identity), None)
    if match:
        merged = {**match, **desired, "id": match["id"]}
        r = client.put(f"/{resource}/{match['id']}", json=merged)
        if r.status_code >= 400:
            log.error("PUT %s/%s failed: %s %s", resource, identity, r.status_code, r.text)
            r.raise_for_status()
        log.info("updated %s: %s", resource, identity)
    else:
        r = client.post(f"/{resource}", json=desired)
        if r.status_code >= 400:
            log.error("POST %s/%s failed: %s %s", resource, identity, r.status_code, r.text)
            r.raise_for_status()
        log.info("created %s: %s", resource, identity)


def sab_download_client_for_arr(sab_url: str, sab_port: int, sab_api_key: str) -> dict[str, Any]:
    """Schema for adding SABnzbd as a download client on an *arr."""
    return {
        "enable": True,
        "name": "SABnzbd",
        "implementation": "Sabnzbd",
        "configContract": "SabnzbdSettings",
        "priority": 1,
        "fields": [
            {"name": "host", "value": sab_url},
            {"name": "port", "value": sab_port},
            {"name": "useSsl", "value": False},
            {"name": "apiKey", "value": sab_api_key},
            {"name": "category", "value": "prowlarr"},
            {"name": "recentTvPriority", "value": -100},
            {"name": "olderTvPriority", "value": -100},
        ],
    }


def prowlarr_application_for_arr(svc: ArrService, prowlarr_url: str) -> dict[str, Any]:
    """Schema for registering an *arr in Prowlarr's Applications table.

    Everything under arrs.<svc>.search in values.yaml gets merged into the
    fields list. That's where syncCategories lives — without it Prowlarr
    has no categories to push searches against and the *arr never sees
    new releases."""
    fields: list[dict[str, Any]] = [
        {"name": "prowlarrUrl", "value": prowlarr_url},
        {"name": "baseUrl", "value": svc.url},
        {"name": "apiKey", "value": svc.api_key},
    ]
    for k, v in svc.search.items():
        fields.append({"name": k, "value": v})
    return {
        "name": svc.impl,
        "syncLevel": "fullSync",
        "implementation": svc.impl,
        "configContract": f"{svc.impl}Settings",
        "fields": fields,
    }


def first_profile_id(client: httpx.Client, resource: str) -> int:
    """Resolve the id of an arbitrary {quality,metadata} profile. *arrs
    ship with defaults seeded by initdb, so the list is non-empty as soon
    as /ping returns 200 — but we still guard with a clear error to avoid
    cryptic KeyErrors if a user has wiped them out."""
    profiles = client.get(f"/{resource}").raise_for_status().json()
    if not profiles:
        raise RuntimeError(f"no {resource} entries; cannot derive a default")
    return profiles[0]["id"]


def root_folder_for_arr(svc: ArrService, client: httpx.Client) -> dict[str, Any]:
    """Desired RootFolder spec. Sonarr/Radarr accept {path} alone; Lidarr/Readarr
    require defaultQualityProfileId + defaultMetadataProfileId which can only be
    learned at runtime from the live *arr (their ids depend on what migrations
    seeded). Readarr additionally needs isCalibreLibrary.

    `path` is resolved by helm into `arrs.<svc>.rootFolderPath` (which defaults
    to `/media/<mediaDir>` but is overridable). For Lidarr/Readarr the `name`
    field is required and shown in the UI; derive it from the path basename."""
    if not svc.root_folder_path:
        raise RuntimeError(f"{svc.name}: rootFolderPath not configured")
    path = svc.root_folder_path
    if svc.name in ("sonarr", "radarr"):
        return {"path": path}
    common = {
        "name": path.rstrip("/").rsplit("/", 1)[-1] or path,
        "path": path,
        "defaultQualityProfileId": first_profile_id(client, "qualityprofile"),
        "defaultMetadataProfileId": first_profile_id(client, "metadataprofile"),
        "defaultMonitorOption": "all",
        "defaultNewItemMonitorOption": "all",
        "defaultTags": [],
    }
    if svc.name == "readarr":
        return {**common, "isCalibreLibrary": False}
    return common  # lidarr


def prowlarr_indexer(spec: dict[str, Any]) -> dict[str, Any]:
    """Pass-through for user-defined indexer specs from values.yaml. The
    indexer's API key is loaded from the env var named in spec.apiKeyEnv;
    any string field equal to "$API_KEY" inside settings is replaced with
    that value (matches the historical convention from the SQL hook)."""
    settings = dict(spec.get("settings", {}))
    api_key_env = spec.get("apiKeyEnv")
    if api_key_env:
        api_key = env(api_key_env)
        for k, v in settings.items():
            if v == "$API_KEY":
                settings[k] = api_key
    fields = [{"name": k, "value": v} for k, v in settings.items()]
    return {
        "enable": spec.get("enabled", True),
        "name": spec["name"],
        "implementation": spec["type"],
        "configContract": f"{spec['type']}Settings",
        "priority": spec.get("priority", 25),
        "fields": fields,
    }


def build_services(config: dict[str, Any]) -> tuple[list[ArrService], ArrService]:
    """Returns (non-prowlarr arrs, prowlarr) — separated because Prowlarr's
    role in reconciliation is different from the others."""
    arrs: list[ArrService] = []
    prowlarr: ArrService | None = None
    for raw in config["arrs"]:
        impl = raw["name"].capitalize()
        svc = ArrService(
            name=raw["name"],
            impl=impl,
            url=raw["url"],
            api_key=env(raw["apiKeyEnv"]),
            api_version=ARR_API_VERSIONS.get(raw["name"], "v1"),
            root_folder_path=raw.get("rootFolderPath"),
            search=raw.get("search") or {},
        )
        if raw["name"] == "prowlarr":
            prowlarr = svc
        else:
            arrs.append(svc)
    if not prowlarr:
        raise RuntimeError("prowlarr entry missing from reconciler config")
    return arrs, prowlarr


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_config()
    arrs, prowlarr = build_services(config)

    ready = wait_for_ready([*arrs, prowlarr])
    ready_names = {s.name for s in ready}
    prowlarr_ready = prowlarr.name in ready_names
    arrs = [a for a in arrs if a.name in ready_names]
    if not prowlarr_ready:
        log.warning(
            "prowlarr not ready; skipping prowlarr-side reconcile "
            "(applications, indexers, download client)"
        )

    sab = config.get("sabnzbd")
    sab_dc: dict[str, Any] | None = None
    if sab and sab.get("enabled", True):
        sab_dc = sab_download_client_for_arr(
            sab_url=sab["host"],
            sab_port=sab["port"],
            sab_api_key=env(sab["apiKeyEnv"]),
        )

    # Wrap individual upserts so one bad row (e.g. Prowlarr rejecting an
    # Application because the target *arr's API schema is incompatible)
    # doesn't abort the whole reconcile. Counts failures so the run still
    # exits non-zero — the CronJob and bootstrap hook then surface a real
    # error in their status without losing the progress made.
    failures: list[str] = []

    def try_upsert(client: httpx.Client, resource: str, desired: dict[str, Any],
                   key: str = "name", context: str = "") -> None:
        identity = desired.get(key, "?")
        try:
            upsert(client, resource, desired, key=key)
        except (httpx.HTTPError, RuntimeError) as e:
            label = f"{context}:{resource}/{identity}" if context else f"{resource}/{identity}"
            log.warning("%s skipped: %s", label, e)
            failures.append(label)

    if prowlarr_ready:
        with arr_client(prowlarr) as c:
            for arr in arrs:
                try_upsert(c, "applications", prowlarr_application_for_arr(arr, prowlarr.url),
                           context="prowlarr")
            for indexer in config.get("indexers", []):
                try_upsert(c, "indexer", prowlarr_indexer(indexer), context="prowlarr")
            if sab_dc:
                try_upsert(c, "downloadclient", sab_dc, context="prowlarr")

    for arr in arrs:
        with arr_client(arr) as c:
            if sab_dc:
                try_upsert(c, "downloadclient", sab_dc, context=arr.name)
            # Root folders depend on profile ids the *arr seeds during its
            # first migration. On a brand-new install the GET /qualityprofile
            # call has occasionally returned empty even after /ping succeeded;
            # log and continue so one slow *arr doesn't block reconciling the
            # others. CronJob will retry on the next tick.
            try:
                upsert(c, "rootfolder", root_folder_for_arr(arr, c), key="path")
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("%s: root folder skipped: %s", arr.name, e)
                failures.append(f"{arr.name}:rootfolder")

    if failures:
        log.error("reconciliation completed with %d failure(s): %s",
                  len(failures), ", ".join(failures))
        return 1
    log.info("reconciliation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
