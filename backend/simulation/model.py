"""
Core simulation model for the ARC hackathon dashboard.

This module implements the closed‑form formulas and related calculations
outlined in the economic policy proposal. It exposes a single entry
point, ``run_simulation``, which accepts a payload describing the
policy parameters and returns a dictionary of computed results. The
implementation covers:

* Provincial multipliers ``k_i`` and aggregate multiplier ``k``
* Money creation ``Delta M = k * G``
* VAT recapture per province and in total
* Venture formation probabilities and counts
* Optional Markov‑chain absorption and effective multiplier
* Optional New Keynesian (NK) price effect sketch

The design is functional: all heavy calculations are confined to this
module so that the API layer remains thin. When new metrics are
introduced, extend the helper functions here.
"""
from __future__ import annotations

import math
from typing import List, Dict, Any

def _compute_k_i(lam: float, L_i: float) -> float:
    """Compute the provincial multiplier k_i given marginal propensity to spend
    ``lam`` and leakage ``L_i``. This uses the geometric series formula
    1 / (1 - lam * (1 - L_i)). Raises ValueError if the term in the
    denominator is non‑positive, which would indicate an infinite or
    negative multiplier.
    """
    denom = 1.0 - lam * (1.0 - L_i)
    if denom <= 0:
        raise ValueError("Invalid parameters: lam * (1 - L_i) must be < 1")
    return 1.0 / denom

def _compute_closed_form(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute closed‑form results: multipliers, money creation and VAT.

    Parameters expected in ``payload``:
    * ``L``: list of 3 leakage shares [L1, L2, L3]
    * ``omega``: list of 3 spending weights [w1, w2, w3]
    * ``lambda``: marginal propensity to spend (0 < lambda < 1)
    * ``tau``: VAT rate (0 <= tau < 1)
    * ``G``: total transfer (number)

    The function assumes ``omega`` sums to 1. ``G_i = omega_i * G``.  It
    returns per‑province multipliers ``k_i``, aggregate multiplier ``k``,
    money creation ``deltaM`` and VAT revenue ``vat``.
    """
    L = payload.get("L", [0.0, 0.0, 0.0])
    omega = payload.get("omega", [1/3, 1/3, 1/3])
    lam = payload.get("lambda", 0.8)
    tau = payload.get("tau", 0.0)
    G = payload.get("G", 0.0)

    if len(L) != 3 or len(omega) != 3:
        raise ValueError("L and omega must each have length 3")

    # Normalize omega in case it does not sum exactly to 1 due to user input
    total_omega = sum(omega)
    if total_omega != 0:
        omega = [w / total_omega for w in omega]

    k_i: List[float] = []
    M_i: List[float] = []
    T_i: List[float] = []
    for i in range(3):
        k_val = _compute_k_i(lam, L[i])
        k_i.append(k_val)
        G_i = omega[i] * G
        M_val = k_val * G_i
        M_i.append(M_val)
        # VAT from province i: tau * G_i * (1 - L_i) / (1 - lam * (1 - L_i))
        vat_i = tau * G_i * ((1.0 - L[i]) / (1.0 - lam * (1.0 - L[i]))) if tau > 0 else 0.0
        T_i.append(vat_i)

    # Aggregate multiplier as weighted average of k_i
    k = sum(omega[i] * k_i[i] for i in range(3))
    deltaM = k * G
    total_vat = sum(T_i)

    return {
        "k_i": k_i,
        "k": k,
        "M_i": M_i,
        "deltaM": deltaM,
        "vat": total_vat,
    }

def _compute_venture(payload: Dict[str, Any], closed_form: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute venture formation metrics.  Liquidity density D_i is defined
    as M_i / P_i where M_i comes from closed form.  P_i defaults to
    ``participants_active / 3`` when not provided.  Venture probability
    P_v,i = alpha0 + alpha1 * D_i + alpha2 * sigma_i^2.  Currently we
    assume sigma_i^2 = 0 (no dispersion effect).  Venture count V_i =
    P_v,i * P_i.

    Parameters expected in ``payload['venture']``:
    * ``alpha0``: base probability
    * ``alpha1``: elasticity wrt liquidity density
    * ``alpha2``: elasticity wrt dispersion (unused if 0)
    * ``participants_active``: total number of active participants
      (defaults to 0 if not provided).  Optionally, a list of three
      values can be provided to specify participants per tier.

    Returns a dict with lists ``D_i``, ``Pv_i``, ``V_i`` and scalar
    ``V`` (total ventures).
    """
    venture_params = payload.get("venture", {})
    alpha0 = venture_params.get("alpha0", 0.0)
    alpha1 = venture_params.get("alpha1", 0.0)
    alpha2 = venture_params.get("alpha2", 0.0)

    participants = venture_params.get("participants_active", 0.0)
    # Accept either a single number or list per tier
    if isinstance(participants, list):
        if len(participants) != 3:
            raise ValueError("participants_active list must have length 3")
        P_i = participants
    else:
        # Distribute evenly across three tiers
        P_i = [participants / 3.0] * 3 if participants else [0.0, 0.0, 0.0]

    M_i = closed_form.get("M_i", [0.0, 0.0, 0.0])
    D_i: List[float] = []
    Pv_i: List[float] = []
    V_i: List[float] = []

    for i in range(3):
        if P_i[i] > 0:
            D_val = M_i[i] / P_i[i]
        else:
            D_val = 0.0
        D_i.append(D_val)
        # For now, omit sigma^2 term; could be extended later
        pv = alpha0 + alpha1 * D_val + alpha2 * 0.0
        # Clip probability between 0 and 1 for safety
        pv = max(0.0, min(1.0, pv))
        Pv_i.append(pv)
        V_i.append(pv * P_i[i])

    total_V = sum(V_i)

    return {
        "D_i": D_i,
        "Pv_i": Pv_i,
        "V_i": V_i,
        "V": total_V,
    }

def _compute_nk(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute a rough New Keynesian price impact band.  The NK model
    suggests inflation responds to the output gap via the Phillips curve.
    Since our hackathon project only sketches this effect, we compute
    a simple range: ``dPi_low = kappa * x`` and ``dPi_high = 1.5 * kappa * x``.

    The ``nk`` sub-dictionary may contain ``x`` (output gap) and
    ``kappa`` (slope).  Defaults: x=0.02, kappa=0.1.
    """
    nk_params = payload.get("nk", {})
    x = nk_params.get("x", 0.02)
    kappa = nk_params.get("kappa", 0.1)
    dPi_low = kappa * x
    dPi_high = 1.5 * kappa * x
    return {
        "dPi_low": dPi_low,
        "dPi_high": dPi_high,
    }

def _invert_3x3(matrix: List[List[float]]) -> List[List[float]]:
    """
    Invert a 3×3 matrix.  Uses the adjugate/determinant formula.  Raises
    ValueError if the matrix is singular (determinant zero).  This
    function does not attempt any numerical stabilisation; since our
    matrices are small and hand‑constructed, this is sufficient.
    """
    # Unpack for readability
    a,b,c = matrix[0]
    d,e,f = matrix[1]
    g,h,i = matrix[2]
    det = (
        a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
    )
    if abs(det) < 1e-12:
        raise ValueError("Matrix is singular and cannot be inverted")
    inv_det = 1.0 / det
    # Compute cofactors and transpose (adjugate)
    inv = [
        [ (e*i - f*h) * inv_det, -(b*i - c*h) * inv_det,  (b*f - c*e) * inv_det ],
        [-(d*i - f*g) * inv_det,  (a*i - c*g) * inv_det, -(a*f - c*d) * inv_det ],
        [ (d*h - e*g) * inv_det, -(a*h - b*g) * inv_det,  (a*e - b*d) * inv_det ],
    ]
    return inv

def _compute_markov(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute Markov absorption shares and effective multiplier if requested.

    The ``markov`` sub-dictionary should contain:
    * ``use``: boolean flag (True to compute, False/absent to skip)
    * ``pi``: 3×3 matrix of routing probabilities between tiers
    * ``ell``: list of 3 leakage shares (per tier)
    * ``s0``: initial distribution over tiers (list of 3 numbers summing to 1)
    * ``tau``: VAT rate (inherited from top-level if absent)
    The marginal propensity ``lambda`` is inherited from top-level payload.

    The function returns ``aVAT`` (absorption into VAT), ``aLEAK`` and
    ``k_eff`` (Markov effective multiplier).  If markov.use is False or
    required parameters are missing, returns an empty dict.
    """
    markov = payload.get("markov", {})
    if not markov or not markov.get("use"):
        return {}
    # Extract parameters with fallbacks
    pi = markov.get("pi")
    ell = markov.get("ell")
    s0 = markov.get("s0")
    tau = payload.get("tau", 0.0)
    lam = payload.get("lambda", 0.8)
    if pi is None or ell is None or s0 is None:
        return {}
    if len(pi) != 3 or any(len(row) != 3 for row in pi):
        return {}
    if len(ell) != 3 or len(s0) != 3:
        return {}
    # Normalise s0
    total_s0 = sum(s0)
    if total_s0 != 0:
        s0 = [x / total_s0 for x in s0]
    # Build Q (3×3) and R (3×2) matrices
    Q = [[0.0]*3 for _ in range(3)]
    R = [[0.0]*2 for _ in range(3)]
    for j in range(3):
        leak_j = ell[j]
        for k in range(3):
            Q[j][k] = (1.0 - tau - leak_j) * pi[j][k]
        R[j][0] = tau
        R[j][1] = leak_j
    # Compute fundamental matrix N = (I - Q)^-1
    I_minus_Q = [[0.0]*3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            I_minus_Q[r][c] = (1.0 if r == c else 0.0) - Q[r][c]
    try:
        N = _invert_3x3(I_minus_Q)
    except ValueError:
        return {}
    # Compute visits v^T = s0^T N (length 3), absorptions a^T = v^T R (length 2)
    # v = s0 @ N
    v = [0.0, 0.0, 0.0]
    for j in range(3):
        for k in range(3):
            v[k] += s0[j] * N[j][k]
    # a = v @ R
    aVAT = 0.0
    aLEAK = 0.0
    for j in range(3):
        aVAT += v[j] * R[j][0]
        aLEAK += v[j] * R[j][1]
    # Effective multiplier per monetary unit: k_eff = 1 + lam * (sum of visits)
    visits_sum = sum(v)
    k_eff = 1.0 + lam * visits_sum
    return {
        "aVAT": aVAT,
        "aLEAK": aLEAK,
        "k_eff": k_eff,
    }

def run_simulation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entrypoint to compute all simulation metrics.  Accepts a
    ``payload`` dictionary with keys described in the helper functions.
    Missing keys will be replaced with sensible defaults.  Returns a
    dictionary with the following structure:

    ```json
    {
      "k": number,
      "k_i": [number, number, number],
      "deltaM": number,
      "vat": number,
      "venture": { "D_i": [...], "Pv_i": [...], "V_i": [...], "V": number },
      "nk": { "dPi_low": number, "dPi_high": number },
      "markov": { "aVAT": number, "aLEAK": number, "k_eff": number } // optional
    }
    ```

    The "echo" of the payload itself is not returned here; the API layer may
    choose to include it separately.  Exceptions raised by helper
    functions will propagate out to the caller.
    """
    # Closed form results
    closed_form = _compute_closed_form(payload)
    # Venture formation results
    venture = _compute_venture(payload, closed_form)
    # NK inflation band
    nk = _compute_nk(payload)
    # Markov results (may return {})
    markov = _compute_markov(payload)
    # Assemble response
    response = {
        "k": closed_form["k"],
        "k_i": closed_form["k_i"],
        "deltaM": closed_form["deltaM"],
        "vat": closed_form["vat"],
        "venture": venture,
        "nk": nk,
    }
    if markov:
        response["markov"] = markov
    return response