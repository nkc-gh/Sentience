"""Daemon IPC Server."""
import os
import socket
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from config import SentienceConfig
from storage.database import Database

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="Sentience Daemon API", lifespan=lifespan)

class PingResponse(BaseModel):
    status: str
    version: str

@app.get("/api/v1/health", response_model=PingResponse)
async def health_check():
    from importlib.metadata import version
    return PingResponse(status="ok", version=version("sentience"))

def serve(config: SentienceConfig, db: Database):
    """Run the Uvicorn server using native IPC for the current platform."""
    if os.name == "nt":
        port = config.daemon_port or 8765
        app.state.config = config
        app.state.db = db
        logger.info("Starting Daemon API on localhost:%d", port)
        server_config = uvicorn.Config(
            app,
            host=config.daemon_host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        uvicorn.Server(server_config).run()
        return

    socket_path = config.socket_path
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        socket_path.parent.chmod(0o700)
    except OSError:
        pass
    
    if socket_path.exists():
        # Check if actually in use
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(socket_path))
                logger.error("Daemon is already running on %s", socket_path)
                return
        except socket.error:
            # Dead socket, remove it
            socket_path.unlink()
            
    # Set app state
    app.state.config = config
    app.state.db = db
    
    logger.info("Starting Daemon API on unix socket %s", socket_path)

    server_config = uvicorn.Config(
        app,
        uds=str(socket_path),
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(server_config)
    previous_umask = os.umask(0o077)
    try:
        server.run()
    finally:
        os.umask(previous_umask)
        try:
            socket_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove daemon socket %s", socket_path)
