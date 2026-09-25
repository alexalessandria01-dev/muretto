"""Calcoli del muretto: funzioni pure su numeri, senza stato.

- ``gap_seconds`` / ``lap_time_seconds``: parsing delle stringhe del feed
- ``pit_loss_for``: tempo perso in pit lane (osservato nel feed o da tabella)
- ``pit_exit``: dove rientra un pilota se entra ai box adesso
- ``degradation``: pendenza dei tempi sul giro nello stint corrente
- ``undercut_threat``: chi è dietro ha la finestra per l'undercut?
"""

from __future__ import annotations

import statistics
from statistics import median

# Tempo perso in pit lane (ingresso → uscita, senza la sosta) per circuito,
# in secondi, valori tipici di gara. ShortName come nel SessionInfo.Circuit.
PIT_LOSS_TABLE = {
    "Melbourne": 20.0, "Shanghai": 23.0, "Suzuka": 22.0, "Sakhir": 23.5, "Jeddah": 20.5,
    "Miami": 20.5, "Imola": 27.0, "Monaco": 19.5, "Montreal": 19.0, "Catalunya": 21.0,
    "Barcelona": 21.0, "Spielberg": 20.0, "Silverstone": 21.0, "Spa": 19.0, "Hungaroring": 21.0,
    "Zandvoort": 20.5, "Monza": 24.0, "Madrid": 21.0, "Baku": 20.0, "Singapore": 27.0,
    "Austin": 21.0, "Mexico City": 22.5, "Interlagos": 21.0, "Las Vegas": 20.0,
    "Lusail": 24.0, "Yas Marina": 22.0,
}
DEFAULT_PIT_LOSS = 22.0


def gap_seconds(text: str | None) -> float | None:
    """``"+12.345"`` → 12.345; leader (``"LAP 12"``) → 0; doppiato (``"1L"``) o vuoto → None.

    Vuoto NON vuol dire leader: la F1 lo manda vuoto anche per chi è secondo (a Monza al giro 22
    Russell risultava primo e la sosta lo dava fuori P10). Il leader lo sceglie chi chiama, dalla
    posizione."""
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    if t.startswith("LAP"):
        return 0.0
    if t.endswith("L") or "LAP" in t:
        return None
    try:
        return float(t.lstrip("+"))
    except ValueError:
        return None


def lap_time_seconds(text: str | None) -> float | None:
    if not text:
        return None
    try:
        if ":" in text:
            m, s = text.split(":")
            return int(m) * 60 + float(s)
        return float(text)
    except ValueError:
        return None


def pit_loss_for(circuit: str | None, observed: list[float]) -> float:
    """Mediana delle pit lane osservate nella sessione, altrimenti la tabella."""
    good = [x for x in observed if 12 <= x <= 40]
    if good:
        return round(median(good), 1)
    return PIT_LOSS_TABLE.get(circuit or "", DEFAULT_PIT_LOSS)


def pit_exit(driver: str, field: list[tuple[str, float | None]], pit_loss: float) -> dict | None:
    """Se ``driver`` entra ai box adesso, dove rientra.

    ``field`` = ``[(numero, gap_al_leader_in_secondi | None), ...]``; i None sono
    doppiati e vengono ignorati (sono comunque dietro).
    """
    gaps = dict(field)
    if driver not in gaps or gaps[driver] is None:
        return None
    exit_gap = gaps[driver] + pit_loss
    others = sorted((g, n) for n, g in gaps.items() if n != driver and g is not None)
    ahead = [(n, g) for g, n in others if g < exit_gap]
    behind = [(n, g) for g, n in others if g >= exit_gap]
    current_pos = 1 + sum(1 for g, _ in others if g < gaps[driver])
    exit_pos = 1 + len(ahead)
    return {
        "exit_gap": round(exit_gap, 3),
        "exit_position": exit_pos,
        "positions_lost": exit_pos - current_pos,
        "behind": (ahead[-1][0], round(exit_gap - ahead[-1][1], 1)) if ahead else None,
        "ahead_of": (behind[0][0], round(behind[0][1] - exit_gap, 1)) if behind else None,
    }


FUEL_PER_LAP = 0.05  # s/giro che si guadagnano bruciando benzina: stima (~0,03 s/kg × ~1,7 kg/giro)
MIN_DEG_LAPS = 4     # con 3 giri la pendenza balla troppo (fino a -0,7 s/giro a inizio stint)


def degradation(laps: list[tuple[int, float, bool]]) -> dict | None:
    """Quanto rallenta la gomma, in s/giro, sui giri puliti ``(giro, secondi, pulito)`` dello stint.

    - si scartano i giri più lenti di oltre 1 s della mediana: a Monza il giro di ripartenza dopo
      la rossa (~200 s) risultava pulito e portava il degrado a -40 s/giro per tutti;
    - pendenza di Theil-Sen (mediana delle pendenze fra coppie): un giro anomalo non la sposta;
    - "slope" è al netto della benzina: la macchina si alleggerisce di ~0,05 s/giro, quindi a
      tempi piatti la gomma sta già perdendo quei 0,05. "raw_slope" è quella dei tempi così come sono.
    """
    pts = [(n, t) for n, t, clean in laps if clean and t]
    if len(pts) < MIN_DEG_LAPS:
        return None
    med = statistics.median(t for _, t in pts)
    pts = [(n, t) for n, t in pts if t <= med + 1.0]
    if len(pts) < MIN_DEG_LAPS:
        return None
    slopes = [(t2 - t1) / (n2 - n1) for i, (n1, t1) in enumerate(pts) for n2, t2 in pts[i + 1:] if n2 != n1]
    raw = statistics.median(slopes) if slopes else 0.0
    intercept = statistics.median(t - raw * n for n, t in pts)
    return {"slope": round(raw + FUEL_PER_LAP, 4), "raw_slope": round(raw, 4), "fuel": FUEL_PER_LAP,
            "intercept": round(intercept, 3), "laps": len(pts), "last_clean": pts[-1][1]}


def undercut_threat(interval_behind: float | None, pit_loss: float, my_tyre_age: int, their_tyre_age: int, margin: float = 3.0) -> dict | None:
    """Il pilota dietro è nella finestra per farmi l'undercut?

    È "in finestra" se, entrando ora, rientrerebbe entro ``margin`` secondi da me
    (``interval < pit_loss + margin``). ``needs_per_lap`` = quanto deve guadagnare
    al giro con gomma nuova per passarmi in un out-lap + in-lap.
    """
    if interval_behind is None:
        return None
    window = interval_behind < pit_loss + margin
    needs = max(0.0, (pit_loss - interval_behind) / 2)  # 2 giri (out-lap suo, in-lap mio)
    return {
        "window": window,
        "interval": interval_behind,
        "needs_per_lap": round(needs, 2),
        "tyre_delta": my_tyre_age - their_tyre_age,
    }


DRY = ("SOFT", "MEDIUM", "HARD")


def compound_rule(compounds: list[str]) -> dict:
    """Regola delle due mescole in gara asciutta: servono almeno due mescole da asciutto
    diverse. Se si montano intermedie o da bagnato la regola non vale più."""
    used = [c for c in dict.fromkeys(compounds) if c in DRY]
    if any(c in ("INTERMEDIATE", "WET") for c in compounds):
        return {"ok": True, "used": used, "wet": True}
    return {"ok": len(used) >= 2, "used": used, "wet": False}


def format_lap(seconds: float | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def theoretical_best(best_sectors: list[str], best_lap: str | None) -> dict | None:
    """Giro teorico = somma dei migliori settori personali; il margine è quanto
    il miglior giro vero ha lasciato sul tavolo rispetto a quella somma."""
    secs = [lap_time_seconds(s) for s in best_sectors]
    if len(secs) != 3 or any(s is None for s in secs):
        return None
    theo = round(sum(secs), 3)
    best = lap_time_seconds(best_lap)
    return {"time": format_lap(theo), "seconds": theo,
            "margin": round(best - theo, 3) if best is not None else None}


def fill_lapped_gaps(rows: list[dict]) -> None:
    """Distacco dal leader in secondi anche per i doppiati.

    La F1 scrive "1 L" come distacco di un doppiato, ma l'intervallo da chi gli sta
    davanti resta in secondi: sommandoli lungo la classifica (``rows`` in ordine di
    posizione) si ricava il distacco vero, e con esso la finestra dei box."""
    prev = None
    for r in rows:
        if r.get("gap_s") is None and prev is not None and prev.get("gap_s") is not None \
                and not r.get("retired") and not r.get("stopped"):
            iv = gap_seconds(r.get("interval"))
            if iv:
                r["gap_s"] = round(prev["gap_s"] + iv, 3)
        prev = r
