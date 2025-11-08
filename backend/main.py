import os
from typing import Optional
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from backend.monitoring.deals_router import router as deals_router


# Optional watcher (won't crash if missing)
try:
    from .watcher.watcher import Watcher  # type: ignore
except Exception:
    Watcher = None  # type: ignore

# Optional simulation router
try:
    from .simulation.router import router as simulation_router  # type: ignore
except Exception:
    simulation_router = None  # type: ignore

# Required: monitoring router
try:
    from .monitoring.router import router as monitoring_router
except Exception as e:
    raise RuntimeError(f"Failed to import monitoring router: {e}")

DB_PATH = os.getenv("DB_PATH", "/data/app.db")

app = FastAPI(title="ARC Hackathon Dashboard", docs_url="/docs", redoc_url="/redoc")
app.include_router(deals_router)

# Optional sim endpoints
if simulation_router:
    app.include_router(simulation_router, prefix="/api/sim")

# Monitoring endpoints (UI reads these)
app.include_router(monitoring_router, prefix="/api/mon")
print("[start] Monitoring router mounted at /api/mon")

@app.get("/healthz")
def health() -> dict[str, bool]:
    return {"ok": True}

# Serve the built frontend (Dockerfile copies to ./frontend_dist)
static_dir = "frontend_dist" if os.path.isdir("frontend_dist") else "frontend"
print(f"[start] Serving static from: {static_dir}")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

@app.on_event("startup")
async def start_watcher() -> None:
    if not Watcher:
        print("[start] Watcher not available; skipping.")
        return
    rpc_url = os.getenv("RPC_URL")
    token_addr = os.getenv("TOKEN_ADDR")
    if not rpc_url or not token_addr:
        print("[start] Watcher not started: RPC_URL and TOKEN_ADDR must be set")
        return
    try:
        tau = float(os.getenv("VAT_RATE", "0.07"))
    except ValueError:
        tau = 0.07
    try:
        lam = float(os.getenv("OBS_LAMBDA", "0.8"))
    except ValueError:
        lam = 0.8

    watcher = Watcher(db_path=DB_PATH, rpc_url=rpc_url, token_addr=token_addr, tau=tau, lam=lam)
    app.state.watcher = watcher
    watcher.start()
    print("Watcher started")

@app.on_event("shutdown")
async def stop_watcher() -> None:
    watcher: Optional["Watcher"] = getattr(app.state, "watcher", None)
    if watcher:
        watcher.stop()
        print("Watcher stopped")
