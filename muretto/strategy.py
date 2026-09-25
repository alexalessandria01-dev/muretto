"""Calcoli del muretto: funzioni pure su numeri, senza stato.

- ``gap_seconds`` / ``lap_time_seconds``: parsing delle stringhe del feed
- ``pit_loss_for``: tempo perso in pit lane (osservato nel feed o da tabella)
- ``pit_exit``: dove rientra un pilota se entra ai box adesso
- ``degradation``: pendenza dei tempi sul giro nello stint corrente
- ``undercut_threat``: chi è dietro ha la finestra per l'undercut?
"""

from __future__ import annotations

import math
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


UNDERCUT_GAIN = 0.8  # s/giro che vale una gomma nuova rispetto a una usata: valore di partenza fisso
UNDERCUT_LAPS = 2    # giri in cui si gioca l'undercut: l'out-lap di chi entra e l'in-lap di chi sta davanti


def undercut_threat(interval_behind: float | None, my_tyre_age: int, their_tyre_age: int,
                    gain: float = UNDERCUT_GAIN, laps: int = UNDERCUT_LAPS) -> dict | None:
    """Il pilota dietro può farmi l'undercut?

    La sosta la pagano tutti e due, quindi non conta: conta se la gomma nuova gli fa recuperare
    il distacco nei giri in cui io sono ancora fuori. Rischio alto se il distacco è sotto il
    60% di quel guadagno, medio sotto il 100%, altrimenti basso (fuori finestra).
    Prima si confrontava il distacco con tutta la sosta (~27 s a Monza): "in finestra" nel 98%
    dei casi, anche con 23 s di distacco.
    """
    if interval_behind is None:
        return None
    reach = gain * laps
    risk = "alto" if interval_behind < reach * 0.6 else "medio" if interval_behind < reach else "basso"
    return {
        "window": risk != "basso",
        "risk": risk,
        "interval": interval_behind,
        "gain_per_lap": gain,
        "reach": round(reach, 2),
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


# ------------------------------------------------------------------ battaglie
# Le storie dei giri sono quelle dello snapshot: [giro, secondi, gap_s, posizione, pulito, box].

CATCH_MIN_RATE = 0.15  # s/giro: sotto è rumore, non un recupero
CATCH_MAX_LAPS = 8     # oltre, la previsione è troppo lontana per valere qualcosa
CATCH_LAPS = 4         # giri puliti di fila che servono per dire qualcosa
DRS_GAP = 1.0          # sotto 1 s chi sta dietro ha il DRS


def interval_history(front: list, back: list) -> list[tuple[int, float, bool]]:
    """Intervallo fra due piloti a fine di ogni giro: ``(giro, intervallo, pulito)``.

    Pulito = giro pulito per tutti e due e nessuno dei due ai box. I doppiati (gap_s None)
    non hanno intervallo e restano fuori."""
    by_lap = {l[0]: l for l in front}
    out = []
    for l in back:
        f = by_lap.get(l[0])
        if f is None or l[2] is None or f[2] is None:
            continue
        out.append((l[0], round(l[2] - f[2], 3), bool(l[4] and f[4] and not l[5] and not f[5])))
    return out


def _clean_run(hist: list[tuple[int, float, bool]]) -> list[tuple[int, float]]:
    """Ultimi giri puliti consecutivi, fino all'ultimo giro: una sosta o una SC azzerano."""
    run = []
    for lap, iv, clean in reversed(hist):
        if not clean or (run and run[-1][0] != lap + 1):
            break
        run.append((lap, iv))
    return run[::-1]


def interval_trend(front: list, back: list, n: int = 3) -> float | None:
    """Di quanto cambia l'intervallo a giro negli ultimi ``n`` giri (negativo = si avvicina chi sta dietro)."""
    pts = interval_history(front, back)
    if len(pts) < 2:
        return None
    last = pts[-(n + 1):]
    return round((last[-1][1] - last[0][1]) / (len(last) - 1), 3)


def catch_forecast(front: list, back: list, interval_now: float | None, total_laps: int | None) -> dict | None:
    """Chi sta dietro recupera abbastanza da arrivare in zona DRS (sotto 1 s) entro pochi giri?

    Pendenza sugli ultimi CATCH_LAPS giri puliti di fila; si mostra solo se recupera più di
    CATCH_MIN_RATE s/giro e ci arriva entro CATCH_MAX_LAPS giri. Se il giro previsto va oltre
    la fine: "non ce la fa". Verificato sulla gara di Monza 2026 (vedi test): è una tendenza,
    non una promessa — circa una volta su sei non si avvera."""
    run = _clean_run(interval_history(front, back))[-CATCH_LAPS:]
    if len(run) < CATCH_LAPS:
        return None
    rate = (run[0][1] - run[-1][1]) / (len(run) - 1)  # positivo = recupera
    iv = interval_now if interval_now is not None else run[-1][1]
    if rate <= CATCH_MIN_RATE or iv is None or iv <= DRS_GAP:
        return None
    laps_needed = (iv - DRS_GAP) / rate
    if laps_needed > CATCH_MAX_LAPS:
        return None
    # dal suo ultimo giro chiuso, non dal contagiri generale: quello è il giro del leader, e chi sta
    # dietro è spesso ancora al giro prima (a Monza la previsione usciva un giro in ritardo)
    lap = run[-1][0] + max(1, math.ceil(laps_needed))
    in_time = None if not total_laps else lap <= total_laps  # TotalLaps può valere 0 per qualche secondo
    return {"rate": round(rate, 2), "laps": round(laps_needed, 1), "lap": lap, "in_time": in_time}


STUCK_MIN, STUCK_MAX = 0.3, 1.5  # s: abbastanza vicino da provarci, non abbastanza per passare


def stuck_laps(front: list, back: list) -> int | None:
    """Da quanti giri puliti di fila chi sta dietro resta fra 0,3 e 1,5 s senza passare.
    Solo da 4 giri in su: prima è una battaglia normale."""
    pos_f = {l[0]: l[3] for l in front}
    pos_b = {l[0]: l[3] for l in back}
    n = 0
    for lap, iv in reversed(_clean_run(interval_history(front, back))):
        if not (STUCK_MIN <= iv <= STUCK_MAX) or not (pos_b.get(lap) and pos_f.get(lap) and pos_b[lap] > pos_f[lap]):
            break
        n += 1
    return n if n >= CATCH_LAPS else None


def trains(rows: list[dict], gap: float = DRS_GAP, size: int = 3) -> list[list[str]]:
    """Trenini: almeno ``size`` macchine di fila, ognuna a meno di ``gap`` s da quella davanti.
    ``rows`` in ordine di posizione, con ``interval_s``; ritirati, fermi e ai box interrompono."""
    out, cur = [], []
    for r in rows:
        out_of_play = r.get("retired") or r.get("stopped") or r.get("inpit")
        iv = r.get("interval_s")
        if cur and not out_of_play and iv is not None and iv < gap:
            cur.append(r["num"])
            continue
        if len(cur) >= size:
            out.append(cur)
        cur = [] if out_of_play else [r["num"]]
    if len(cur) >= size:
        out.append(cur)
    return out
