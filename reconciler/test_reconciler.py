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
