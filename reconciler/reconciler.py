"""Idempotent REST reconciler for the mega-media stack.

Reads /etc/reconciler/config.yaml (from a ConfigMap) plus API keys mounted
as env vars, then converges Prowlarr's Applications/Indexers/DownloadClients
and each *arr's DownloadClient against the desired state. Safe to re-run
on a CronJob schedule; each resource is keyed by name and PUT in place
rather than recreated.

Bypasses raw DB inserts (the chart's previous approach) — talks to the
documented REST APIs, which are stable across major *arr versions where
the DB schema is not.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx
import yaml

log = logging.getLogger("reconciler")


@dataclass
class ArrService:
    name: str           # "sonarr"
    impl: str           # "Sonarr" (Prowlarr's Implementation/ConfigContract prefix)
    url: str            # http://mega-sonarr.media.svc.cluster.local:8989
    api_key: str
    api_version: str    # "v3" for sonarr/radarr, "v1" for lidarr/readarr


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


def wait_for_ready(services: list[ArrService], timeout: int = 300) -> None:
    """Poll each /ping until all return 200 or timeout. *arr instances refuse
    POSTs while their DB migrations are still running, so this avoids racing
    a fresh install."""
    deadline = time.time() + timeout
    pending = list(services)
    while pending and time.time() < deadline:
        still_pending: list[ArrService] = []
        for svc in pending:
            try:
                r = httpx.get(f"{svc.url}/ping", timeout=5)
                if r.status_code == 200:
                    log.info("ready: %s", svc.name)
                    continue
            except httpx.RequestError as e:
                log.debug("not ready %s: %s", svc.name, e)
            still_pending.append(svc)
        if still_pending:
            time.sleep(3)
        pending = still_pending
    if pending:
        names = ", ".join(s.name for s in pending)
        raise RuntimeError(f"services did not become ready: {names}")


def arr_client(svc: ArrService) -> httpx.Client:
    return httpx.Client(
        base_url=f"{svc.url}/api/{svc.api_version}",
        headers={"X-Api-Key": svc.api_key},
        timeout=30,
    )


def upsert(client: httpx.Client, resource: str, desired: dict[str, Any]) -> None:
    """GET /api/<ver>/<resource>, find by name, PUT if present, POST if not."""
    name = desired["name"]
    existing = client.get(f"/{resource}").raise_for_status().json()
    match = next((r for r in existing if r.get("name") == name), None)
    if match:
        merged = {**match, **desired, "id": match["id"]}
        r = client.put(f"/{resource}/{match['id']}", json=merged)
        if r.status_code >= 400:
            log.error("PUT %s/%s failed: %s %s", resource, name, r.status_code, r.text)
            r.raise_for_status()
        log.info("updated %s: %s", resource, name)
    else:
        r = client.post(f"/{resource}", json=desired)
        if r.status_code >= 400:
            log.error("POST %s/%s failed: %s %s", resource, name, r.status_code, r.text)
            r.raise_for_status()
        log.info("created %s: %s", resource, name)


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
    """Schema for registering an *arr in Prowlarr's Applications table."""
    return {
        "name": svc.impl,
        "syncLevel": "fullSync",
        "implementation": svc.impl,
        "configContract": f"{svc.impl}Settings",
        "fields": [
            {"name": "prowlarrUrl", "value": prowlarr_url},
            {"name": "baseUrl", "value": svc.url},
            {"name": "apiKey", "value": svc.api_key},
            {"name": "syncCategories", "value": []},
        ],
    }


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

    wait_for_ready([*arrs, prowlarr])

    sab = config.get("sabnzbd")
    sab_dc: dict[str, Any] | None = None
    if sab and sab.get("enabled", True):
        sab_dc = sab_download_client_for_arr(
            sab_url=sab["host"],
            sab_port=sab["port"],
            sab_api_key=env(sab["apiKeyEnv"]),
        )

    with arr_client(prowlarr) as c:
        for arr in arrs:
            upsert(c, "applications", prowlarr_application_for_arr(arr, prowlarr.url))
        for indexer in config.get("indexers", []):
            upsert(c, "indexer", prowlarr_indexer(indexer))
        if sab_dc:
            upsert(c, "downloadclient", sab_dc)

    if sab_dc:
        for arr in arrs:
            with arr_client(arr) as c:
                upsert(c, "downloadclient", sab_dc)

    log.info("reconciliation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
