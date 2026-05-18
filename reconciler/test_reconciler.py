"""Unit tests for reconciler.py. Run with `pytest` from the reconciler/ dir.

Mocks HTTP at the transport layer (httpx.MockTransport) rather than monkeypatching
module-level globals — that way we exercise the real httpx client code, just with
canned responses.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

import reconciler as r


# ---------------------------------------------------------------------------
# upsert(): GET list, find by name, POST if missing, PUT if present.
# ---------------------------------------------------------------------------

def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://arr.test/api/v3",
        headers={"X-Api-Key": "k"},
    )


def test_upsert_posts_when_resource_is_missing():
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json=[])
        if req.method == "POST":
            return httpx.Response(201, json={"id": 7, **json.loads(req.content)})
        return httpx.Response(500)

    with mock_client(handler) as c:
        r.upsert(c, "indexer", {"name": "NZBGeek", "implementation": "Newznab"})

    methods = [m for m, _ in calls]
    assert methods == ["GET", "POST"], "missing resource must trigger GET then POST"


def test_upsert_puts_when_resource_exists():
    calls: list[tuple[str, str, dict | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.method == "GET":
            return httpx.Response(200, json=[
                {"id": 42, "name": "NZBGeek", "implementation": "Newznab", "priority": 25},
            ])
        if req.method == "PUT":
            return httpx.Response(200, json=body)
        return httpx.Response(500)

    with mock_client(handler) as c:
        r.upsert(c, "indexer", {"name": "NZBGeek", "implementation": "Newznab", "priority": 50})

    put_call = next(call for call in calls if call[0] == "PUT")
    method, path, body = put_call
    assert path == "/api/v3/indexer/42"
    assert body["id"] == 42                # existing id preserved
    assert body["priority"] == 50          # desired field wins over existing


def test_upsert_matches_by_name_not_by_id():
    """Even if a row has the same id but different name, upsert should treat
    it as missing and create. This guards against accidental row overwrites
    when ids collide across reconciler runs against a recreated database."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "Other"}])
        if req.method == "POST":
            return httpx.Response(201, json={"id": 99, **json.loads(req.content)})
        pytest.fail(f"unexpected request: {req.method} {req.url}")

    with mock_client(handler) as c:
        r.upsert(c, "indexer", {"name": "NZBGeek"})


# ---------------------------------------------------------------------------
# prowlarr_indexer(): substitutes "$API_KEY" in settings from the env var
# named by spec.apiKeyEnv. Matches the chart's historical convention.
# ---------------------------------------------------------------------------

def test_prowlarr_indexer_substitutes_api_key_in_settings(monkeypatch):
    monkeypatch.setenv("INDEXER_42_API_KEY", "the-secret")

    out = r.prowlarr_indexer({
        "name": "NZBGeek",
        "type": "Newznab",
        "priority": 25,
        "apiKeyEnv": "INDEXER_42_API_KEY",
        "settings": {"baseUrl": "https://x", "apiKey": "$API_KEY", "vipExpiration": ""},
    })

    fields = {f["name"]: f["value"] for f in out["fields"]}
    assert fields["apiKey"] == "the-secret"
    assert fields["baseUrl"] == "https://x"      # unrelated fields untouched
    assert fields["vipExpiration"] == ""         # empty string isn't $API_KEY


def test_prowlarr_indexer_without_apikeyenv_passes_settings_through():
    out = r.prowlarr_indexer({
        "name": "x",
        "type": "Newznab",
        "settings": {"apiKey": "$API_KEY", "baseUrl": "u"},
    })
    fields = {f["name"]: f["value"] for f in out["fields"]}
    # No apiKeyEnv → no substitution. $API_KEY is sent literally, which
    # would fail at the Prowlarr API, but that's a config error to surface,
    # not silently swallow.
    assert fields["apiKey"] == "$API_KEY"


def test_prowlarr_indexer_shapes_implementation_and_config_contract():
    out = r.prowlarr_indexer({"name": "x", "type": "Newznab", "settings": {}})
    assert out["implementation"] == "Newznab"
    assert out["configContract"] == "NewznabSettings"


# ---------------------------------------------------------------------------
# build_services(): separates the prowlarr entry from the rest and resolves
# API keys + API versions per-arr.
# ---------------------------------------------------------------------------

def test_build_services_separates_prowlarr(monkeypatch):
    for name in ("SONARR", "PROWLARR"):
        monkeypatch.setenv(f"ARR_{name}_API_KEY", f"key-{name}")
    config = {"arrs": [
        {"name": "sonarr", "url": "http://s", "apiKeyEnv": "ARR_SONARR_API_KEY"},
        {"name": "prowlarr", "url": "http://p", "apiKeyEnv": "ARR_PROWLARR_API_KEY"},
    ]}
    arrs, prowlarr = r.build_services(config)
    assert [a.name for a in arrs] == ["sonarr"]
    assert prowlarr.name == "prowlarr"
    assert prowlarr.url == "http://p"
    assert prowlarr.api_key == "key-PROWLARR"


def test_build_services_resolves_api_versions(monkeypatch):
    for name in ("SONARR", "LIDARR", "PROWLARR"):
        monkeypatch.setenv(f"ARR_{name}_API_KEY", "k")
    config = {"arrs": [
        {"name": "sonarr", "url": "http://s", "apiKeyEnv": "ARR_SONARR_API_KEY"},
        {"name": "lidarr", "url": "http://l", "apiKeyEnv": "ARR_LIDARR_API_KEY"},
        {"name": "prowlarr", "url": "http://p", "apiKeyEnv": "ARR_PROWLARR_API_KEY"},
    ]}
    arrs, prowlarr = r.build_services(config)
    by_name = {a.name: a for a in arrs}
    assert by_name["sonarr"].api_version == "v3"
    assert by_name["lidarr"].api_version == "v1"
    # Prowlarr's API root happens to be /api/v1 too:
    assert prowlarr.api_version == "v1"


def test_build_services_raises_without_prowlarr(monkeypatch):
    monkeypatch.setenv("ARR_SONARR_API_KEY", "k")
    config = {"arrs": [{"name": "sonarr", "url": "http://s", "apiKeyEnv": "ARR_SONARR_API_KEY"}]}
    with pytest.raises(RuntimeError, match="prowlarr"):
        r.build_services(config)


def test_env_raises_clearly_on_missing(monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET_XYZ", raising=False)
    with pytest.raises(RuntimeError, match="DEFINITELY_NOT_SET_XYZ"):
        r.env("DEFINITELY_NOT_SET_XYZ")


# ---------------------------------------------------------------------------
# Resource shape helpers — pure data, just verifying the contract the *arr
# REST APIs expect.
# ---------------------------------------------------------------------------

def test_sab_download_client_for_arr_shape():
    out = r.sab_download_client_for_arr("http://sab.svc", 8080, "key123")
    assert out["implementation"] == "Sabnzbd"
    assert out["configContract"] == "SabnzbdSettings"
    fields = {f["name"]: f["value"] for f in out["fields"]}
    assert fields["host"] == "http://sab.svc"
    assert fields["port"] == 8080
    assert fields["apiKey"] == "key123"
    assert fields["category"] == "prowlarr"


def test_prowlarr_application_for_arr_shape():
    svc = r.ArrService(
        name="sonarr", impl="Sonarr",
        url="http://sonarr.svc:8989", api_key="abc", api_version="v3",
    )
    out = r.prowlarr_application_for_arr(svc, prowlarr_url="http://prowlarr:9696")
    assert out["name"] == "Sonarr"
    assert out["implementation"] == "Sonarr"
    assert out["configContract"] == "SonarrSettings"
    fields = {f["name"]: f["value"] for f in out["fields"]}
    assert fields["baseUrl"] == "http://sonarr.svc:8989"
    assert fields["prowlarrUrl"] == "http://prowlarr:9696"
    assert fields["apiKey"] == "abc"


def test_prowlarr_application_merges_search_settings():
    """v1 hardcoded syncCategories=[], which meant Prowlarr never pushed any
    category to the *arr and searches never reached the indexer. v2 merges
    the whole arrs.<svc>.search block — that's the regression guard."""
    svc = r.ArrService(
        name="sonarr", impl="Sonarr",
        url="http://s", api_key="k", api_version="v3",
        search={
            "syncCategories": [5000, 5040, 5070],
            "animeSyncCategories": [5070],
            "syncAnimeStandardFormatSearch": True,
            "syncRejectBlocklistedTorrentHashesWhileGrabbing": True,
        },
    )
    out = r.prowlarr_application_for_arr(svc, "http://p")
    fields = {f["name"]: f["value"] for f in out["fields"]}
    assert fields["syncCategories"] == [5000, 5040, 5070]
    assert fields["animeSyncCategories"] == [5070]
    assert fields["syncAnimeStandardFormatSearch"] is True
    assert fields["syncRejectBlocklistedTorrentHashesWhileGrabbing"] is True


def test_prowlarr_application_with_empty_search_omits_extra_fields():
    svc = r.ArrService(
        name="prowlarr", impl="Prowlarr",
        url="http://p", api_key="k", api_version="v1",
    )
    out = r.prowlarr_application_for_arr(svc, "http://p")
    names = [f["name"] for f in out["fields"]]
    assert names == ["prowlarrUrl", "baseUrl", "apiKey"]


# ---------------------------------------------------------------------------
# Root folders: Sonarr/Radarr take just {path}; Lidarr/Readarr need profile
# ids resolved from the live *arr; Readarr additionally needs isCalibreLibrary.
# ---------------------------------------------------------------------------

def _profile_handler(req: httpx.Request) -> httpx.Response:
    """Mock GET handler that returns one quality + one metadata profile."""
    if req.url.path.endswith("/qualityprofile"):
        return httpx.Response(200, json=[{"id": 1, "name": "Standard"}])
    if req.url.path.endswith("/metadataprofile"):
        return httpx.Response(200, json=[{"id": 7, "name": "Default"}])
    return httpx.Response(404)


def test_root_folder_sonarr_is_path_only():
    svc = r.ArrService(
        name="sonarr", impl="Sonarr", url="http://s", api_key="k",
        api_version="v3", root_folder_path="/media/tvshows",
    )
    # No client calls expected — pass a transport that fails loudly.
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: pytest.fail(f"unexpected: {req.url}")),
        base_url="http://s/api/v3",
    )
    with client:
        out = r.root_folder_for_arr(svc, client)
    assert out == {"path": "/media/tvshows"}


def test_root_folder_uses_value_as_given():
    """When the operator overrides rootFolderPath in values.yaml the reconciler
    passes it through verbatim — no /media/ prefix added, no normalization."""
    svc = r.ArrService(
        name="radarr", impl="Radarr", url="http://r", api_key="k",
        api_version="v3", root_folder_path="/storage/movies/4k",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: pytest.fail(f"unexpected: {req.url}")),
        base_url="http://r/api/v3",
    )
    with client:
        out = r.root_folder_for_arr(svc, client)
    assert out == {"path": "/storage/movies/4k"}


def test_root_folder_lidarr_resolves_profiles_from_live_arr():
    svc = r.ArrService(
        name="lidarr", impl="Lidarr", url="http://l", api_key="k",
        api_version="v1", root_folder_path="/media/music",
    )
    with httpx.Client(transport=httpx.MockTransport(_profile_handler),
                      base_url="http://l/api/v1") as client:
        out = r.root_folder_for_arr(svc, client)
    assert out["path"] == "/media/music"
    assert out["name"] == "music"             # derived from path basename
    assert out["defaultQualityProfileId"] == 1
    assert out["defaultMetadataProfileId"] == 7
    assert "isCalibreLibrary" not in out


def test_root_folder_lidarr_derives_name_from_overridden_path():
    """When rootFolderPath is overridden to a deeper path the displayed name
    follows the last segment — not the *arr type."""
    svc = r.ArrService(
        name="lidarr", impl="Lidarr", url="http://l", api_key="k",
        api_version="v1", root_folder_path="/data/audio/lossless",
    )
    with httpx.Client(transport=httpx.MockTransport(_profile_handler),
                      base_url="http://l/api/v1") as client:
        out = r.root_folder_for_arr(svc, client)
    assert out["name"] == "lossless"


def test_root_folder_readarr_includes_isCalibreLibrary():
    svc = r.ArrService(
        name="readarr", impl="Readarr", url="http://b", api_key="k",
        api_version="v1", root_folder_path="/media/books",
    )
    with httpx.Client(transport=httpx.MockTransport(_profile_handler),
                      base_url="http://b/api/v1") as client:
        out = r.root_folder_for_arr(svc, client)
    assert out["isCalibreLibrary"] is False
    assert out["defaultQualityProfileId"] == 1


def test_root_folder_raises_when_path_missing():
    svc = r.ArrService(
        name="sonarr", impl="Sonarr", url="http://s", api_key="k",
        api_version="v3", root_folder_path=None,
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)),
                          base_url="http://s/api/v3")
    with client, pytest.raises(RuntimeError, match="rootFolderPath"):
        r.root_folder_for_arr(svc, client)


def test_first_profile_id_raises_clearly_on_empty():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])
    with httpx.Client(transport=httpx.MockTransport(handler),
                      base_url="http://x/api/v1") as client:
        with pytest.raises(RuntimeError, match="qualityprofile"):
            r.first_profile_id(client, "qualityprofile")


# ---------------------------------------------------------------------------
# upsert(): the `key` parameter lets RootFolders be matched by path.
# ---------------------------------------------------------------------------

def test_upsert_can_match_by_path_for_rootfolders():
    """Sonarr's RootFolder collection has no `name` field — entries are
    identified by their `path`. The key= parameter exists so this works."""
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json=[
                {"id": 3, "path": "/media/tvshows", "accessible": True},
            ])
        if req.method == "PUT":
            return httpx.Response(200, json=json.loads(req.content))
        return httpx.Response(500)

    with mock_client(handler) as c:
        r.upsert(c, "rootfolder", {"path": "/media/tvshows"}, key="path")

    assert [m for m, _ in calls] == ["GET", "PUT"]
    assert calls[-1][1] == "/api/v3/rootfolder/3"


# ---------------------------------------------------------------------------
# wait_for_ready(): unreachable services should be skipped, not abort the run.
# ---------------------------------------------------------------------------

def _svc(name: str, url: str) -> r.ArrService:
    return r.ArrService(
        name=name, impl=name.capitalize(), url=url,
        api_key="k", api_version="v3",
    )


def test_wait_for_ready_returns_only_reachable_services(monkeypatch):
    """One *arr unreachable shouldn't starve the others. wait_for_ready
    returns the subset that became ready; caller filters."""
    reachable = _svc("sonarr", "http://sonarr.test")
    broken = _svc("readarr", "http://readarr.test")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "sonarr.test":
            return httpx.Response(200, text="pong")
        # readarr never comes up
        raise httpx.ConnectError("connection refused", request=req)

    # Patch httpx.get to route through a MockTransport-backed client so we
    # exercise the real retry loop without hitting the network.
    transport = httpx.MockTransport(handler)
    real_get = httpx.get

    def fake_get(url, **kwargs):
        with httpx.Client(transport=transport) as c:
            return c.get(url, **kwargs)

    monkeypatch.setattr(r.httpx, "get", fake_get)
    monkeypatch.setattr(r.time, "sleep", lambda _s: None)

    ready = r.wait_for_ready([reachable, broken], timeout=1)
    assert [s.name for s in ready] == ["sonarr"]


def test_upsert_raises_on_4xx(monkeypatch):
    """upsert() itself stays strict — it's the caller's job to decide
    whether one bad row should abort. main() wraps it in try/except."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=[])
        # POST is rejected (e.g. *arr API schema mismatch)
        return httpx.Response(400, json={"error": "BaseUrl invalid"})

    with mock_client(handler) as c, pytest.raises(httpx.HTTPStatusError):
        r.upsert(c, "applications", {"name": "Readarr"})
