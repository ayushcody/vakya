"""The machine, the engine registry and engine routing (Gnani's Timbre is the only voice), and the health and readiness
bodies. Pure logic plus a few API checks through the TestClient. Nothing is sent anywhere (Supabase health is stubbed)."""
import platform

import pytest

LOCAL = {"storage-mode": "local"}


@pytest.fixture()
def backend_mod(app_client):
    from core.runtime import runtime as backend

    backend.limiter.reset()  # the endpoints are rate limited; start each test with a fresh window
    return backend


# ---- platform_info ---------------------------------------------------------------------------------

def test_platform_info_is_just_the_machine(backend_mod):
    info = backend_mod.platform_info()
    assert set(info) == {"os", "os_version", "arch"}
    assert info["os"] in {"macOS", "Windows", "Linux", platform.system() or "Unknown"}


@pytest.mark.parametrize("raw, expected", [
    ("Darwin", "macOS"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
    ("FreeBSD", "FreeBSD"),  # unknown systems fall back to the raw name
])
def test_platform_info_maps_system_name(backend_mod, monkeypatch, raw, expected):
    monkeypatch.setattr(platform, "system", lambda: raw)
    assert backend_mod.platform_info()["os"] == expected


# ---- route_engine ----------------------------------------------------------------------------------

@pytest.mark.parametrize("language", ["en", "hi", "mr", "bn", "ta", "te", "kn", "ml", "gu", "pa", "hi-en", "es"])
def test_a_preset_voice_is_always_timbre(backend_mod, language):
    assert backend_mod.route_engine(language, "fast", False) == "timbre"


@pytest.mark.parametrize("language", ["en", "hi", "mr"])
def test_a_saved_voice_is_always_timbre_clone(backend_mod, language):
    assert backend_mod.route_engine(language, "premium", True) == backend_mod.CLONE_ENGINE == "timbre_clone"


def test_routing_never_fails_for_want_of_a_key(backend_mod, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    assert backend_mod.route_engine("hi", "fast", True) == "timbre_clone"      # the key is checked when it speaks


# ---- engine registry -------------------------------------------------------------------------------

def test_the_registry_has_the_two_timbre_engines_only(backend_mod):
    assert set(backend_mod.ENGINE_REGISTRY) == {"timbre", "timbre_clone"}
    assert backend_mod.CLONE_ENGINES == ("timbre_clone",)
    assert set(backend_mod.ENGINE_ROUTE_REASON) == {"timbre", "timbre_clone"}


def test_the_engine_list_says_whether_each_can_run(app_client, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    rows = {r["id"]: r for r in app_client.get("/api/engines").json()}
    assert set(rows) == {"timbre", "timbre_clone"}
    assert rows["timbre"]["available"] is False and "Needs a Gnani key" in rows["timbre"]["unavailable_reason"]
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    rows = {r["id"]: r for r in app_client.get("/api/engines").json()}
    assert rows["timbre"]["available"] is True and rows["timbre_clone"]["licence"] == "Gnani AI API terms"


# ---- API -------------------------------------------------------------------------------------------

def test_health_has_no_gpu_or_platform_and_reports_the_language_model(app_client, backend_mod, monkeypatch):
    async def unreachable():  # never hit Supabase from a test
        return False

    monkeypatch.setattr(backend_mod, "supabase_reachable", unreachable)
    body = app_client.get("/api/health", headers=LOCAL).json()
    assert body["status"] == "ok" and "gpu" not in body and "platform" not in body and "models_loaded" not in body
    assert body["llm"]["active"] is None and body["llm"]["providers"] == [
        {"name": "hosted", "label": "Hosted language model", "available": False,
         "reason": "Needs a language model: set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL in backend/.env"}]
    assert body["gnani"]["configured"] is False
    monkeypatch.setenv("LLM_API_KEY", "test-key"); monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1"); monkeypatch.setenv("LLM_MODEL", "test-model")
    llm = app_client.get("/api/health", headers=LOCAL).json()["llm"]
    assert llm["active"] == "hosted" and llm["model"] and llm["providers"][0]["available"] is True


def test_ready_lists_the_missing_keys(app_client, monkeypatch):
    body = app_client.get("/api/ready").json()
    assert body["llm_configured"] is False and body["gnani_configured"] is False and body["ok"] is False
    assert any("set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL" in p for p in body["problems"]) and any("GNANI_API_KEY" in p for p in body["problems"])


def test_the_resource_snapshot_reports_only_what_exists(app_client):
    body = app_client.get("/api/system/resources", headers=LOCAL).json()
    assert body["models"] == [] and {s["id"] for s in body["services"]} == {"gnani", "llm", "embeddings"}
    assert body["system"]["total_mb"] > 0 and "gpu" not in body["system"]
    assert app_client.post("/api/system/models/x/unload", headers=LOCAL).status_code in (404, 405)


def test_the_removed_endpoints_are_gone(app_client):
    for method, path in (("POST", "/api/hardware/mode"), ("POST", "/api/models/preload"), ("POST", "/api/llm/warm")):
        assert app_client.request(method, path, headers=LOCAL, json={}).status_code in (404, 405)
