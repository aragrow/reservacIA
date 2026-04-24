from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.db import init_db
from app.routers import auth, debug, reservations, rooms, tables
from app.security import IPAllowlistMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()

# When running in LOCAL_MODE we override the default /docs below, so have
# FastAPI skip its built-in one to avoid a route-registration conflict.
app = FastAPI(
    title="reservacIA",
    description="Restaurant reservation API for a single AI agent consumer.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.local_mode else "/docs",
)

app.add_middleware(IPAllowlistMiddleware)

app.include_router(auth.router)
app.include_router(rooms.router)
app.include_router(tables.router)
app.include_router(reservations.router)
if settings.local_mode:
    app.include_router(debug.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- local-dev convenience: auto-authorize Swagger UI -------------------------

# Injected after Swagger UI loads. Calls /_debug/dev-token, feeds the token
# into `ui.preauthorizeApiKey(...)`, and also pre-fills the request-body forms
# for /auth/token and /auth/refresh with the env values and a fresh refresh
# token, so "Try it out" on those endpoints works without any typing.
_AUTO_AUTH_SNIPPET = """
<script>
(function() {
  const waitForUI = setInterval(function() {
    if (!window.ui || !window.ui.preauthorizeApiKey) return;
    clearInterval(waitForUI);
    fetch('/_debug/dev-token')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        window.ui.preauthorizeApiKey('HTTPBearer', data.access_token);
        console.log('[reservacIA] local mode: Authorize pre-filled with fresh token');
        // Stash for convenience (devtools access).
        window.__reservaciaDevTokens = data;
      })
      .catch(function(err) { console.warn('[reservacIA] dev-token fetch failed', err); });
  }, 100);
})();
</script>
"""


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui() -> HTMLResponse:
    base = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="reservacIA — docs",
        swagger_ui_parameters={"persistAuthorization": True},
    )
    html = base.body.decode()
    if settings.local_mode:
        html = html.replace("</body>", _AUTO_AUTH_SNIPPET + "</body>")
    return HTMLResponse(html)
