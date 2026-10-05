"""Minimal FastAPI app showing how to wire in the idle auto-stop."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from auto_stop import ActivityMiddleware, AutoStop

logging.basicConfig(level=logging.INFO)

auto_stop = AutoStop()  # reads AUTO_STOP_IDLE_MINUTES / AUTO_STOP_MAX_HOURS from the environment


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto_stop.start()
    try:
        yield
    finally:
        await auto_stop.shutdown()


app = FastAPI(lifespan=lifespan)

# Requests to these paths don't count as user activity.
app.add_middleware(
    ActivityMiddleware,
    tracker=auto_stop.tracker,
    ignore_path_prefixes=("/health",),
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return {"message": "Hello! Each request resets the idle timer."}
