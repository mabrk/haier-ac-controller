"""
Haier AC Web Server
SmartHQ bridge + web UI in one process.

Start:  uvicorn main:app --host 0.0.0.0 --port 8765
Config: copy .env.example → .env and fill in credentials.
Open:   http://<server-ip>:8765
"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)

USERNAME  = os.getenv("SMARTHQ_USERNAME", "").strip()
PASSWORD  = os.getenv("SMARTHQ_PASSWORD", "").strip()
DEVICE_ID = os.getenv("SMARTHQ_DEVICE_ID", "").strip()
REGION    = os.getenv("SMARTHQ_REGION", "US").strip()

_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    if USERNAME and PASSWORD:
        from haier_smarthq import SmartHQClient
        _client = SmartHQClient(USERNAME, PASSWORD, DEVICE_ID, REGION)
        await _client.start()
        log.info("SmartHQ connected. Device ID in use: %s", DEVICE_ID or "(see /api/devices)")
    else:
        log.warning("SMARTHQ_USERNAME / SMARTHQ_PASSWORD not set — edit .env and restart.")
    yield
    if _client:
        await _client.stop()


app = FastAPI(title="Haier AC", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _require_client():
    if _client is None:
        raise HTTPException(503, "SmartHQ credentials not configured — edit .env and restart")
    return _client


# ── UI ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


# ── API ───────────────────────────────────────────────────────────

@app.get("/api/devices")
async def list_devices():
    """List all SmartHQ appliances on your account. Use this to find your device ID."""
    return _require_client().list_devices()


@app.get("/api/status")
async def get_status():
    c = _require_client()
    try:
        return c.get_status()
    except RuntimeError as e:
        raise HTTPException(503, str(e))


class ControlRequest(BaseModel):
    power: Literal["on", "off"] | None = None
    mode:  Literal["cool", "eco", "fan"] | None = None
    temp:  int | None = Field(default=None, ge=60, le=86)
    fan:   Literal["auto", "low", "medium", "high"] | None = None


@app.post("/api/control")
async def control(req: ControlRequest):
    c = _require_client()
    if not any([req.power, req.mode, req.temp, req.fan]):
        raise HTTPException(400, "Provide at least one field to change")
    try:
        return await c.control(req.power, req.mode, req.temp, req.fan)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
