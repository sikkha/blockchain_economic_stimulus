"""
Simulation API router for computing policy scenarios.  Endpoints defined
in this module should accept JSON payloads describing the simulation
parameters and return computed results based on the economic model.  This
placeholder illustrates how to define a router; replace the example
implementation with real logic as you develop the simulation submodule.
"""
from fastapi import APIRouter, HTTPException

# Import the simulation model entrypoint.  This provides the core
# economic calculations described in the policy proposal.
from .model import run_simulation as model_run_simulation

router = APIRouter()

@router.get("/presets")
async def get_presets() -> dict:
    """
    Return a list of preset parameter sets for the simulation tab.  These
    presets correspond to scenarios outlined in the economic policy paper.
    The returned dictionary has a single key ``presets`` whose value
    is a list of objects with ``name`` and ``params`` keys.  Each ``params``
    object contains the leakage vector ``L``, spending weights ``omega``,
    marginal propensity ``lambda``, VAT rate ``tau`` and transfer ``G``.
    """
    presets = [
        {
            "name": "Baseline (open)",
            "params": {
                "L": [0.7, 0.7, 0.7],
                "omega": [1/3, 1/3, 1/3],
                "lambda": 0.8,
                "tau": 0.07,
                "G": 300_000_000_000,
            },
        },
        {
            "name": "Thai‑Boosty (tiered)",
            "params": {
                "L": [0.0, 0.5, 0.7],
                "omega": [0.3, 0.5, 0.2],
                "lambda": 0.8,
                "tau": 0.07,
                "G": 300_000_000_000,
            },
        },
        {
            "name": "Optimized regional",
            "params": {
                "L": [0.3, 0.3, 0.3],
                "omega": [0.4, 0.4, 0.2],
                "lambda": 0.8,
                "tau": 0.07,
                "G": 300_000_000_000,
            },
        },
    ]
    return {"presets": presets}

@router.post("/run")
async def run_simulation(payload: dict) -> dict:
    """
    Compute the simulation based on supplied parameters.  The request
    body should contain keys defined in the simulation model.  On
    success, returns a dictionary with multipliers, money creation,
    VAT, venture and optional Markov/NK metrics.  If an error occurs
    during computation (e.g., invalid parameters), a 400 response is
    generated with the error message.
    """
    try:
        results = model_run_simulation(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return results
