"""
Pull API — FastAPI server that exposes crawled price data to the CMS.

Endpoints:
  GET  /health                  — liveness check
  GET  /prices?sku=X            — list crawled prices for a SKU (newest first)
  GET  /prices?sku=X&limit=N    — same, with custom page size (max 200)
  POST /crawl/trigger           — trigger a new crawl run (async, returns run_id)
  GET  /crawl/status/{run_id}   — check crawl run status

Auth:
  If PULL_API_TOKEN is set, every request must include:
    Authorization: Bearer <token>
  If unset, no auth is required (development mode — a warning is logged at startup).

Crawl trigger behaviour:
  - Only one crawl can run at a time. A 409 is returned if one is already running.
  - The crawl runs in a background thread (spider uses asyncio.run() internally,
    which is safe to call from a thread even when FastAPI's own event loop is live).
  - Poll /crawl/status/{run_id} to know when it finishes.

Response envelope (prices):
  {
    "status": "ok",
    "sku": "SKU001",
    "data": [
      {
        "price": 1299000.0,
        "source": "dienmayxanh.com",
        "crawled_at": "2026-04-13T10:30:15.123456Z",
        "status": "OK",
        "error": null,
        "run_id": "2026-04-13T10:30:00.000001Z"
      }
    ],
    "meta": {"total": 1, "limit": 50}
  }

Usage:
    app = create_app(repository=repo, api_token="secret", crawl_config={...})
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from scraper.storage.database import ResultRepository

logger = logging.getLogger(__name__)

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


# ── Crawl state tracking ────────────────────────────────────────────────────────

@dataclass
class _RunState:
    run_id: str
    status: str          # "running" | "done" | "error"
    error_msg: str | None = None


# ── App factory ────────────────────────────────────────────────────────────────

_CRAWL_INTERVAL_HOURS = 2


def create_app(
    repository: ResultRepository,
    api_token: Optional[str] = None,
    crawl_config: Optional[dict] = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        repository:   Initialised ResultRepository.
        api_token:    Optional Bearer token. If None, auth is disabled with a warning.
        crawl_config: Dict with keys ``cms_api_url``, ``cms_api_token``,
                      ``proxy_list`` — required to enable POST /crawl/trigger.
                      If None, the trigger endpoint returns 503.

    Returns:
        Configured FastAPI app ready to be passed to uvicorn.
    """
    if not api_token:
        logger.warning(
            "Pull API running WITHOUT authentication. "
            "Set PULL_API_TOKEN in .env for production use."
        )

    _config = crawl_config or {}

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # noqa: ANN001
        scheduler = BackgroundScheduler(daemon=True)

        def _scheduled_crawl() -> None:
            from scraper.crawler import CrawlError, run_crawl
            cms_url = _config.get("cms_api_url")
            cms_token = _config.get("cms_api_token")
            if not cms_url or not cms_token:
                logger.warning("Scheduled crawl skipped: CMS credentials not configured")
                return
            logger.info("Scheduled crawl starting (every %dh)", _CRAWL_INTERVAL_HOURS)
            try:
                run_id, _ = run_crawl(
                    cms_api_url=cms_url,
                    cms_api_token=cms_token,
                    proxy_list=_config.get("proxy_list", []),
                    repo=repository,
                )
                logger.info("Scheduled crawl finished (run_id=%s)", run_id)
            except CrawlError as exc:
                logger.error("Scheduled crawl failed: %s", exc)
            except Exception as exc:
                logger.error("Scheduled crawl unexpected error: %s", exc, exc_info=True)

        scheduler.add_job(
            _scheduled_crawl,
            trigger="interval",
            hours=_CRAWL_INTERVAL_HOURS,
            id="auto_crawl",
            next_run_time=datetime.now(),  # chạy ngay khi server start
        )
        scheduler.start()
        logger.info(
            "Auto-crawl scheduler started — runs every %dh", _CRAWL_INTERVAL_HOURS
        )

        yield

        scheduler.shutdown(wait=False)

    app = FastAPI(
        title="Price Crawler — Pull API",
        description="Query crawled product prices by SKU and trigger crawl runs.",
        version="1.0.0",
        lifespan=_lifespan,
    )

    _register_routes(
        app,
        repository=repository,
        api_token=api_token,
        crawl_config=_config,
    )

    return app


# ── Route registration ─────────────────────────────────────────────────────────

def _register_routes(
    app: FastAPI,
    repository: ResultRepository,
    api_token: Optional[str],
    crawl_config: dict,
) -> None:
    # Single-worker pool: only one crawl at a time
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crawl")
    _lock = threading.Lock()
    _current: list[_RunState] = []   # 0 or 1 items; list used for mutability in closure

    # ── Auth dependency ────────────────────────────────────────────────────────

    def _check_auth(request: Request) -> None:
        """Validate Bearer token when api_token is configured."""
        if not api_token:
            return
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = auth_header.removeprefix("Bearer ").strip()
        if token != api_token:
            raise HTTPException(status_code=403, detail="Invalid token")

    # ── /health ────────────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict:
        """Liveness probe — returns 200 if the server is running."""
        return {"status": "ok"}

    # ── /prices ────────────────────────────────────────────────────────────────

    @app.get("/prices")
    def get_prices(
        sku: str = Query(..., description="Product SKU to query"),
        limit: int = Query(
            default=_DEFAULT_LIMIT,
            ge=1,
            le=_MAX_LIMIT,
            description=f"Max results to return (1–{_MAX_LIMIT})",
        ),
        _auth: None = Depends(_check_auth),
    ) -> JSONResponse:
        """Return the most recent crawled prices for a given SKU, newest first."""
        rows = repository.get_prices_by_sku(sku=sku, limit=limit)
        return JSONResponse(content={
            "status": "ok",
            "sku": sku,
            "data": rows,
            "meta": {"total": len(rows), "limit": limit},
        })

    # ── POST /crawl/trigger ────────────────────────────────────────────────────

    @app.post("/crawl/trigger")
    def trigger_crawl(
        _auth: None = Depends(_check_auth),
    ) -> JSONResponse:
        """Trigger a new crawl run in the background.

        Returns immediately with a ``run_id``. Poll
        ``GET /crawl/status/{run_id}`` to check completion, then query
        ``GET /prices?sku=X`` to retrieve the updated prices.

        Returns 409 if a crawl is already running.
        Returns 503 if CMS API credentials are not configured.
        """
        cms_url = crawl_config.get("cms_api_url")
        cms_token = crawl_config.get("cms_api_token")

        if not cms_url or not cms_token:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Crawl trigger not available: CMS_API_URL or CMS_API_TOKEN "
                    "is not configured. Set them in .env and restart the server."
                ),
            )

        with _lock:
            if _current and _current[0].status == "running":
                raise HTTPException(
                    status_code=409,
                    detail=f"A crawl is already running (run_id={_current[0].run_id}). "
                           "Wait for it to finish before triggering another.",
                )

            # Placeholder — actual run_id assigned inside the thread after start_run()
            state = _RunState(run_id="pending", status="running")
            _current.clear()
            _current.append(state)

        def _do_crawl() -> None:
            from scraper.crawler import CrawlError, run_crawl
            try:
                run_id, _ = run_crawl(
                    cms_api_url=cms_url,
                    cms_api_token=cms_token,
                    proxy_list=crawl_config.get("proxy_list", []),
                    repo=repository,
                )
                with _lock:
                    state.run_id = run_id
                    state.status = "done"
            except CrawlError as exc:
                logger.error("Crawl trigger failed: %s", exc)
                with _lock:
                    state.status = "error"
                    state.error_msg = str(exc)
            except Exception as exc:
                logger.error("Unexpected crawl error: %s", exc, exc_info=True)
                with _lock:
                    state.status = "error"
                    state.error_msg = f"Unexpected error: {exc}"

        _executor.submit(_do_crawl)

        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "message": "Crawl started in background.",
                "poll_url": "/crawl/status/current",
            },
        )

    # ── GET /crawl/status/current ──────────────────────────────────────────────

    @app.get("/crawl/status/current")
    def crawl_status_current(
        _auth: None = Depends(_check_auth),
    ) -> JSONResponse:
        """Check the status of the most recent crawl run."""
        with _lock:
            if not _current:
                return JSONResponse(content={
                    "status": "idle",
                    "message": "No crawl has been triggered yet.",
                })
            state = _current[0]

        return JSONResponse(content={
            "status": state.status,
            "run_id": state.run_id,
            "error": state.error_msg,
        })

    # ── Generic error handler ──────────────────────────────────────────────────

    @app.exception_handler(Exception)
    async def _generic_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error on %s: %s", request.url, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Internal server error"},
        )
