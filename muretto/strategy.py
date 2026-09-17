"""Calcoli del muretto: funzioni pure su numeri, senza stato.

- ``gap_seconds`` / ``lap_time_seconds``: parsing delle stringhe del feed
- ``pit_loss_for``: tempo perso in pit lane (osservato nel feed o da tabella)
- ``pit_exit``: dove rientra un pilota se entra ai box adesso
- ``degradation``: pendenza dei tempi sul giro nello stint corrente
- ``undercut_threat``: chi è dietro ha la finestra per l'undercut?
"""

from __future__ import annotations

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
    """``"+12.345"`` → 12.345; leader (``"LAP 12"``/vuoto) → 0; doppiato (``"1L"``) → None."""
    if text is None:
        return 0.0
    t = text.strip()
    if not t or t.startswith("LAP"):
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


def degradation(laps: list[tuple[int, float, bool]]) -> dict | None:
    """Retta dei minimi quadrati sui giri puliti ``(giro, secondi, pulito)`` dello stint.

    Ritorna pendenza (s/giro), intercetta, numero di giri usati e ultimo giro pulito.
    """
    pts = [(n, t) for n, t, clean in laps if clean and t]
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0.0
    return {"slope": round(slope, 4), "intercept": round(my - slope * mx, 3), "laps": len(pts), "last_clean": ys[-1]}


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
