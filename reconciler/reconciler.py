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
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import yaml

try:  # optional: the process runs fine without it, metrics just go dark
    import prometheus_client
except ImportError:  # pragma: no cover - exercised only when the dep is absent
    prometheus_client = None  # type: ignore[assignment]

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
    min_version: str | None = None             # optional known-good floor; below it we warn about possible schema/API drift


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


def upsert(client: httpx.Client, resource: str, desired: dict[str, Any],
           key: str = "name", skip_if_exists: bool = False) -> None:
    """GET /api/<ver>/<resource>, find by `key` (default "name"), PUT if present,
    POST if not. Use key="path" for RootFolders, whose natural identity is the
    filesystem path rather than a display name.

    Set skip_if_exists=True for resources the *arr API treats as immutable
    (e.g. Radarr's RootFolders only accept POST/DELETE — PUT returns 405).
    A path that already exists is already correct; nothing to update."""
    identity = desired.get(key)
    existing = client.get(f"/{resource}").raise_for_status().json()
    match = next((r for r in existing if r.get(key) == identity), None)
    if match:
        if skip_if_exists:
            log.info("exists %s: %s (skip)", resource, identity)
            return
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


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version ("4.0.1.929") into a comparable tuple.
    Non-numeric segments (e.g. a build hash) stop the parse — we only compare
    the leading numeric prefix, which is enough for a known-good floor."""
    parts: list[int] = []
    for seg in version.split("."):
        if seg.isdigit():
            parts.append(int(seg))
        else:
            break
    return tuple(parts)


def check_version(client: httpx.Client, svc: ArrService) -> str | None:
    """Advisory preflight: log the *arr's running version and warn if it's below
    the configured known-good floor (svc.min_version). Never raises and never
    blocks the reconcile — the reconciler relies on the REST *contract*, which is
    stable across minor versions where the DB schema is not, so this is a
    diagnostic signal for drift, not a gate. Returns the version string."""
    try:
        status = client.get("/system/status").raise_for_status().json()
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        log.warning("%s: could not read /system/status: %s", svc.name, e)
        return None
    if not isinstance(status, dict):
        log.warning("%s: /system/status returned unexpected shape", svc.name)
        return None
    version = status.get("version")
    log.info("%s version: %s", svc.name, version)
    if svc.min_version and version and (
        _version_tuple(version) < _version_tuple(svc.min_version)
    ):
        log.warning(
            "%s version %s is below the known-good floor %s — reconcile may hit "
            "schema/API drift; pin the image or raise the floor once verified",
            svc.name, version, svc.min_version,
        )
    return version


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
            min_version=raw.get("minVersion"),
        )
        if raw["name"] == "prowlarr":
            prowlarr = svc
        else:
            arrs.append(svc)
    if not prowlarr:
        raise RuntimeError("prowlarr entry missing from reconciler config")
    return arrs, prowlarr


class Telemetry:
    """Prometheus surface for the reconcile loop. Degrades to no-ops when
    prometheus_client isn't installed so the reconciler never hard-depends on
    it (unit tests, minimal images). `ready` is flipped True by main() once the
    loop is up and serving, backing /readyz (see main() for why not "first pass
    completed")."""

    def __init__(self) -> None:
        self.ready = False
        self._enabled = prometheus_client is not None
        if not self._enabled:
            return
        # A dedicated registry (not the global default) so constructing more
        # than one Telemetry — in tests, or a future refactor — never trips
        # "Duplicated timeseries in CollectorRegistry".
        self._registry = prometheus_client.CollectorRegistry()
        G = prometheus_client.Gauge
        reg = self._registry
        self._runs = prometheus_client.Counter(
            "megamedia_reconcile_runs_total", "Reconcile iterations attempted", registry=reg)
        self._last_ts = G(
            "megamedia_last_reconcile_timestamp_seconds",
            "Unix time of the last completed reconcile", registry=reg)
        self._duration = G(
            "megamedia_reconcile_duration_seconds", "Duration of the last reconcile", registry=reg)
        self._failures = G(
            "megamedia_reconcile_failures", "Per-resource failures in the last reconcile",
            registry=reg)
        self._success = G(
            "megamedia_reconcile_success",
            "1 if the last reconcile had zero failures, else 0", registry=reg)
        self._reachable = G(
            "megamedia_service_reachable", "1 if the *arr answered /ping", ["service"],
            registry=reg)

    def record_run(self, duration: float, failures: int, now: float) -> None:
        self.ready = True
        if not self._enabled:
            return
        self._runs.inc()
        self._last_ts.set(now)
        self._duration.set(duration)
        self._failures.set(failures)
        self._success.set(0 if failures else 1)

    def set_reachable(self, service: str, ok: bool) -> None:
        if self._enabled:
            self._reachable.labels(service=service).set(1 if ok else 0)

    def render(self) -> tuple[bytes, str]:
        if not self._enabled:
            return b"# prometheus_client not installed\n", "text/plain"
        return (prometheus_client.generate_latest(self._registry),
                prometheus_client.CONTENT_TYPE_LATEST)


def serve_health(telemetry: Telemetry, port: int) -> ThreadingHTTPServer:
    """Tiny always-on HTTP surface: /healthz (liveness), /readyz (gated on the
    first completed reconcile) and /metrics. Runs in a daemon thread so it never
    blocks shutdown. Deliberately dependency-free — a stdlib handler, not a web
    framework, which is where Python containers actually get heavy."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._reply(200, b"ok")
            elif path == "/readyz":
                self._reply(200, b"ready") if telemetry.ready else self._reply(503, b"not ready")
            elif path == "/metrics":
                body, ctype = telemetry.render()
                self._reply(200, body, ctype)
            else:
                self._reply(404, b"not found")

        def log_message(self, *_args: Any) -> None:  # silence per-request logging
            pass

    httpd = ThreadingHTTPServer(("", port), Handler)
    threading.Thread(target=httpd.serve_forever, name="health", daemon=True).start()
    log.info("health/metrics server listening on :%d (/healthz /readyz /metrics)", port)
    return httpd


def reconcile_once(config: dict[str, Any], telemetry: Telemetry | None = None) -> list[str]:
    """Run a single convergence pass. Returns the list of per-resource failure
    labels (empty on full success). Never raises for per-resource errors — each
    upsert is wrapped so one bad row doesn't abort the rest. Safe to call
    repeatedly from the loop; each call re-resolves ready services."""
    arrs, prowlarr = build_services(config)

    wait_timeout = int(os.environ.get("WAIT_TIMEOUT", "300"))
    ready = wait_for_ready([*arrs, prowlarr], timeout=wait_timeout)
    ready_names = {s.name for s in ready}
    if telemetry:
        for svc in (*arrs, prowlarr):
            telemetry.set_reachable(svc.name, svc.name in ready_names)
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
    # doesn't abort the whole reconcile. Counts failures for the metrics /
    # summary without losing the progress made.
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
            check_version(c, prowlarr)
            for arr in arrs:
                try_upsert(c, "applications", prowlarr_application_for_arr(arr, prowlarr.url),
                           context="prowlarr")
            for indexer in config.get("indexers", []):
                try_upsert(c, "indexer", prowlarr_indexer(indexer), context="prowlarr")
            if sab_dc:
                try_upsert(c, "downloadclient", sab_dc, context="prowlarr")

    for arr in arrs:
        with arr_client(arr) as c:
            check_version(c, arr)
            if sab_dc:
                try_upsert(c, "downloadclient", sab_dc, context=arr.name)
            # Root folders depend on profile ids the *arr seeds during its
            # first migration. On a brand-new install the GET /qualityprofile
            # call has occasionally returned empty even after /ping succeeded;
            # log and continue so one slow *arr doesn't block reconciling the
            # others. The next loop tick retries.
            try:
                upsert(c, "rootfolder", root_folder_for_arr(arr, c),
                       key="path", skip_if_exists=True)
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("%s: root folder skipped: %s", arr.name, e)
                failures.append(f"{arr.name}:rootfolder")

    if failures:
        log.error("reconciliation completed with %d failure(s): %s",
                  len(failures), ", ".join(failures))
    else:
        log.info("reconciliation complete")
    return failures


def _truthy(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    oneshot = _truthy("RECONCILE_ONESHOT")
    interval = int(os.environ.get("RECONCILE_INTERVAL", "300"))
    metrics_port = int(os.environ.get("METRICS_PORT", "8000"))

    # SIGTERM/SIGINT set the stop event; the loop wakes from its sleep and exits
    # cleanly so Kubernetes rollouts don't wait out the termination grace period.
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_a: stop.set())

    telemetry = Telemetry()
    if not oneshot:
        serve_health(telemetry, metrics_port)
        # Readiness = "the loop is up and serving", not "a reconcile finished".
        # The first pass can block in wait_for_ready polling *arrs that are still
        # starting; gating readiness on that would wedge the pod un-ready (and
        # its readiness probe failing) for minutes on a fresh install.
        telemetry.ready = True

    while not stop.is_set():
        started = time.time()
        # Reload config every iteration: the ConfigMap is a volume mount (not a
        # subPath), so kubelet syncs edits in place and the loop picks them up
        # with no pod restart.
        try:
            failures = reconcile_once(load_config(), telemetry)
            telemetry.record_run(time.time() - started, len(failures), time.time())
        except Exception:  # noqa: BLE001 - a loop must not die on a transient error
            log.exception("reconcile iteration failed; retrying next tick")
        if oneshot:
            break
        # Interruptible sleep: SIGTERM during the wait returns immediately.
        stop.wait(interval)

    log.info("reconciler exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
