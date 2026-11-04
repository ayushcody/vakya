"""
Vakya - phone receptionist backend: agents and their documents, the spoken conversation turn (speech recognition,
retrieval, the language model, speech synthesis), consented voice cloning and the signed receipts.

This module is only the app: the FastAPI instance, its middleware, the routers and the start-up and shut-down hooks. The
work is in the feature routers (routers/), the services they call (services/) and the shared state and configuration
(core/state.py, core/engines.py). `core.runtime.runtime` is the one object that stands for the whole.

Run with: uvicorn backend:app --host 0.0.0.0 --port 8020 --reload
"""

import importlib
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from core import engines, ratelimit, state
    from core.errors import register_exception_handlers
    from core.logging_mw import install_logging_middleware
    from routers import agents, auth, documents, system, turns, voices, warmup
except ImportError:  # pragma: no cover - imported as backend.backend
    from backend.core import engines, ratelimit, state
    from backend.core.errors import register_exception_handlers
    from backend.core.logging_mw import install_logging_middleware
    from backend.routers import agents, auth, documents, system, turns, voices, warmup

log = logging.getLogger("vakya")

print(f"[Vakya] Platform: {state.PLATFORM['os']} {state.PLATFORM['os_version']} ({state.PLATFORM['arch']})")


# ============================================================================
# The app
# ============================================================================

app = FastAPI(
    title="Vakya",
    description="Phone receptionist API: agents, documents, spoken turns, bookings and signed voice receipts",
    version=state.APP_VERSION
)

if ratelimit.SLOWAPI:
    from slowapi.middleware import SlowAPIMiddleware
    app.state.limiter = ratelimit.limiter

register_exception_handlers(app)

# Middleware order: later add_middleware calls are OUTER. Logging first (innermost) so that
# error responses it produces still receive CORS headers.
install_logging_middleware(app)
if ratelimit.SLOWAPI:
    app.add_middleware(SlowAPIMiddleware)

# CORS middleware for Next.js frontend. Origins come from CORS_ORIGINS (comma list).
# Credentials are never combined with a wildcard origin.
_CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3010").split(",") if o.strip()] \
    or ["http://localhost:3010"]
_CORS_CREDENTIALS = "*" not in _CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=_CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Receipt-Id"],
)


# ============================================================================
# Routers
# ============================================================================

# The routes of the server itself, voices, agents, documents and turns, in the order they were always registered (a path
# that two routes could both match goes to the first one).
for _feature in (system, auth, voices, agents, documents, turns):
    app.include_router(_feature.router)

# The routers that have their own module of routes (the optional hook `install` lets one add routes to the main app too, as
# the phone router does for its public routes).
for _name in ("templates", "consent", "verify", "warmup", "phone", "conversations", "privacy",
              "system_resources", "web_call", "callers", "bookings", "schedules", "delays", "usage", "theatre", "hearing"):
    try:
        _mod = importlib.import_module(f"routers.{_name}")
        app.include_router(_mod.router)
        getattr(_mod, "install", lambda _app: None)(app)
    except Exception as _e:  # noqa: BLE001 - a broken router must not crash the app
        log.error("Optional router 'routers.%s' not loaded: %s", _name, _e, exc_info=True)


# ============================================================================
# Start-up and shut-down
# ============================================================================

@app.on_event("startup")
async def on_startup():
    warmup.start_background_warmup()
    if "PYTEST_CURRENT_TEST" not in os.environ:  # the tests build the phone-only app themselves (tests/test_phone_public.py)
        try:
            from phone_public import server as phone_public_server
        except ImportError:  # pragma: no cover
            from backend.phone_public import server as phone_public_server
        try:
            port = await phone_public_server.start()
            if port:
                print(f"[Vakya] Phone-only public port {port}: the call stream and status callback, nothing else")
        except Exception as e:  # noqa: BLE001 - the main API must start even if the second port cannot bind
            log.error("phone-only public port did not start: %s", e)
        heartbeat = system.heartbeat_service()
        reason = heartbeat.start()
        if reason is None:
            print(f"[Vakya] Heartbeat every {heartbeat.status()['interval_s']:.0f} s to {heartbeat.status()['target']}")


app.on_event("startup")(voices.retention_startup)  # the voices past their retention period are deleted at start-up


@app.on_event("shutdown")
async def on_shutdown():
    try:
        from phone_public import server as phone_public_server
    except ImportError:  # pragma: no cover
        from backend.phone_public import server as phone_public_server
    await phone_public_server.stop()
    await system.heartbeat_service().stop()


if __name__ == "__main__":
    import uvicorn

    print("\n" + "="*50)
    print("[Vakya] FastAPI Backend Started")
    print("="*50)
    print(f"Supported Models: {list(engines.ENGINE_REGISTRY.keys())}")
    print("="*50 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("VAKYA_API_PORT", "8020")))
