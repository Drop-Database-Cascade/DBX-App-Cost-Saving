"""Idle auto-stop for Databricks Apps.

Stops a Databricks App through the Apps API once nobody has used it for a set
period, so you stop paying for compute that is sitting idle. Works with any
ASGI framework (FastAPI, Starlette, ...).

The app authenticates with its own service principal, whose credentials
(DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET / DATABRICKS_HOST) the Apps
runtime injects automatically. That service principal needs CAN MANAGE on the
app to be allowed to stop it.

Configuration (environment variables, e.g. in app.yaml):
    AUTO_STOP_IDLE_MINUTES  minutes without user activity before stopping (default 60, 0 = off)
    AUTO_STOP_MAX_HOURS     optional hard cap on runtime in hours (default 0 = off)
    DATABRICKS_APP_NAME     injected by the Apps runtime; override with APP_NAME if needed
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.error("Auto-stop: invalid %s=%r; using default %s", name, raw, default)
        return default


# --------------------------------------------------------------------------- #
# Activity tracking
# --------------------------------------------------------------------------- #
class IdleTracker:
    """Remembers when the last user activity happened (per process)."""

    def __init__(self) -> None:
        self._last = time.monotonic()

    def touch(self) -> None:
        self._last = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last


class ActivityMiddleware:
    """ASGI middleware that records HTTP requests and WebSocket messages as activity.

    ignore_path_prefixes: paths that should NOT count as activity, such as health
        checks or endpoints your frontend polls automatically. If a page polls the
        backend while a tab is open, the app will never look idle unless you list
        that endpoint here.
    require_user_header: if True, only requests carrying the user identity
        headers added by the Databricks Apps proxy (X-Forwarded-Email /
        X-Forwarded-User) count. Check those headers reach your app first.
    """

    def __init__(
        self,
        app,
        tracker: IdleTracker,
        ignore_path_prefixes: tuple[str, ...] = ("/health",),
        require_user_header: bool = False,
    ) -> None:
        self.app = app
        self.tracker = tracker
        self.ignore_path_prefixes = tuple(ignore_path_prefixes)
        self.require_user_header = require_user_header

    def _is_user_traffic(self, scope) -> bool:
        if scope.get("path", "").startswith(self.ignore_path_prefixes):
            return False
        if self.require_user_header:
            names = {k.lower() for k, _ in scope.get("headers") or []}
            return b"x-forwarded-email" in names or b"x-forwarded-user" in names
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not self._is_user_traffic(scope):
            await self.app(scope, receive, send)
            return

        self.tracker.touch()

        if scope["type"] == "websocket":
            original_receive = receive

            async def receive():  # noqa: F811 - wraps the original receive
                message = await original_receive()
                if message["type"] == "websocket.receive":
                    self.tracker.touch()
                return message

        try:
            await self.app(scope, receive, send)
        finally:
            # Also record the end, so a long request can't be cut off mid-flight.
            self.tracker.touch()


# --------------------------------------------------------------------------- #
# Stopping the app
# --------------------------------------------------------------------------- #
async def stop_app(app_name: str, reason: str, attempts: int = 3, retry_delay_s: float = 30) -> None:
    """Stop the app via the Apps API, retrying before falling back to SIGTERM.

    The API call is the only path that stops the whole app and its compute, so
    it is retried first. SIGTERM only ends *this* process: if your app runs
    more than one process (e.g. a Node frontend in front of a Python API),
    the others keep running.
    """
    logger.warning("Auto-stop: %s; stopping app '%s'", reason, app_name)
    for attempt in range(1, attempts + 1):
        try:
            from databricks.sdk import WorkspaceClient

            # The SDK call is blocking, so run it off the event loop.
            await asyncio.to_thread(WorkspaceClient().apps.stop, app_name)
            logger.info("Auto-stop: stop request for '%s' accepted", app_name)
            return
        except Exception:
            logger.exception("Auto-stop: stop attempt %d/%d failed", attempt, attempts)
            if attempt < attempts:
                await asyncio.sleep(retry_delay_s)

    logger.error("Auto-stop: all API attempts failed; sending SIGTERM as a fallback")
    os.kill(os.getpid(), signal.SIGTERM)


async def stop_when_idle(app_name: str, idle_minutes: float, tracker: IdleTracker) -> None:
    """Stop the app once there has been no activity for *idle_minutes*."""
    limit_s = idle_minutes * 60
    # Check every 60s normally, more often for short test values (minimum 5s).
    check_interval_s = max(5.0, min(60.0, limit_s / 4))
    tracker.touch()  # the idle clock starts when the app starts
    while True:
        await asyncio.sleep(check_interval_s)
        idle_s = tracker.idle_seconds()
        if idle_s >= limit_s:
            await stop_app(app_name, f"no user activity for {idle_s / 60:.1f} min")
            return


async def stop_after_hours(app_name: str, hours: float) -> None:
    """Optional hard cap: stop the app after *hours*, regardless of activity."""
    await asyncio.sleep(hours * 3600)
    await stop_app(app_name, f"{hours:g}-hour runtime limit reached")


# --------------------------------------------------------------------------- #
# Convenience wrapper
# --------------------------------------------------------------------------- #
class AutoStop:
    """Owns the tracker and the background tasks. Start it in your app's lifespan."""

    def __init__(
        self,
        app_name: str | None = None,
        idle_minutes: float | None = None,
        max_hours: float | None = None,
    ) -> None:
        self.app_name = app_name or os.environ.get("APP_NAME") or os.environ.get("DATABRICKS_APP_NAME")
        self.idle_minutes = (
            idle_minutes if idle_minutes is not None else _env_float("AUTO_STOP_IDLE_MINUTES", 60.0)
        )
        self.max_hours = max_hours if max_hours is not None else _env_float("AUTO_STOP_MAX_HOURS", 0.0)
        self.tracker = IdleTracker()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if not self.app_name:
            logger.warning("Auto-stop disabled: no app name (DATABRICKS_APP_NAME / APP_NAME not set)")
            return
        if self.idle_minutes > 0:
            self._tasks.append(
                asyncio.create_task(stop_when_idle(self.app_name, self.idle_minutes, self.tracker))
            )
            logger.info("Auto-stop: '%s' will stop after %g min idle", self.app_name, self.idle_minutes)
        if self.max_hours > 0:
            self._tasks.append(asyncio.create_task(stop_after_hours(self.app_name, self.max_hours)))
            logger.info("Auto-stop: '%s' will stop after %g h regardless", self.app_name, self.max_hours)

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
