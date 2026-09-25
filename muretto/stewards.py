"""Commissari e limiti della pista, ricavati dai messaggi della direzione gara.

Il feed non ha un canale per le investigazioni: stanno nel testo dei messaggi,
per esempio::

    TURN 4 INCIDENT INVOLVING CARS 30 (LAW) AND 27 (HUL) NOTED (15:53:37)
    FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 30 (LAW) AND 27 (HUL) UNDER INVESTIGATION (15:53:37)
    BLACK AND WHITE FLAG FOR CAR 30 (LAW) (15:53:37)
    FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 11 (PER) - FAILING TO FOLLOW ...
    CAR 23 (ALB) TIME 2:12.275 DELETED - TRACK LIMITS AT TURN 1 LAP 6 15:46:00

I messaggi dello stesso incidente condividono l'orario tra parentesi, oppure
macchine e motivo: così si segue un incidente da "notato" fino all'esito.
"""

from __future__ import annotations

import re

CARS = re.compile(r"\bCARS?\s+((?:\d+\s+\([A-Z]{3}\)(?:\s*(?:,|AND)\s*)?)+)")
CAR = re.compile(r"(\d+)\s+\(([A-Z]{3})\)")
TIME_TAG = re.compile(r"\((\d{2}:\d{2}:\d{2})\)\s*$")
TURN = re.compile(r"\bTURN (\d+)\b")
TRACK_LIMIT = re.compile(r"CAR (\d+) \([A-Z]{3}\) (?:LAP|TIME [\d:.]+) DELETED - TRACK LIMITS AT TURN (\d+)")
PENALTY = re.compile(r"(\d+ SECOND (?:TIME|STOP/GO) PENALTY|DRIVE THROUGH PENALTY|STOP AND GO PENALTY|GRID PENALTY|DISQUALIFIED)")

# stato → (etichetta, chiuso?)
STATUS = [
    ("NO FURTHER", ("nessuna azione", True)),
    ("REPRIMAND", ("reprimenda", True)),
    ("BLACK AND WHITE FLAG", ("bandiera bianco-nera", True)),
    ("WARNING", ("avvertimento", True)),
    ("WILL BE INVESTIGATED AFTER", ("in esame dopo la gara", False)),
    ("UNDER INVESTIGATION", ("in esame", False)),
    ("NOTED", ("notato", False)),
]


def _penalty_label(p: str) -> str:
    n = re.match(r"(\d+) SECOND", p)
    if "STOP/GO" in p or "STOP AND GO" in p:
        return f"stop & go{' ' + n.group(1) + ' s' if n else ''}"
    if n:
        return f"{n.group(1)} s di penalità"
    return {"DRIVE THROUGH PENALTY": "drive through", "GRID PENALTY": "penalità in griglia",
            "DISQUALIFIED": "squalifica"}.get(p, p.lower())


def _status(msg: str) -> tuple[str, bool] | None:
    m = PENALTY.search(msg)
    if m:
        return _penalty_label(m.group(1)), True
    for key, val in STATUS:
        if key in msg:
            return val
    return None


def _reason(msg: str) -> str:
    """Il motivo dopo il primo " - ", senza l'orario finale."""
    if " - " not in msg:
        return ""
    return TIME_TAG.sub("", msg.split(" - ", 1)[1]).strip()


def _cars(msg: str) -> list[str]:
    m = CARS.search(msg) or re.search(r"FOR CAR\s+(\d+\s+\([A-Z]{3}\))", msg)
    return [n for n, _ in CAR.findall(m.group(1))] if m else []


def analyse(messages: list[dict]) -> dict:
    """Da tutti i messaggi della sessione (in ordine): incidenti e conti per pilota.

    ``{"incidents": [...], "drivers": {num: {"track_limits", "penalties", "open"}}}``.
    Ogni incidente: ``{"cars", "turn", "reason", "status", "closed", "lap", "history"}``.
    """
    incidents: list[dict] = []
    drivers: dict[str, dict] = {}

    def drv(num: str) -> dict:
        return drivers.setdefault(num, {"track_limits": 0, "penalties": [], "open": 0})

    for m in messages:
        msg = str(m.get("Message", "")).strip()
        tl = TRACK_LIMIT.search(msg)
        if tl:
            drv(tl.group(1))["track_limits"] += 1
            continue
        st = _status(msg)
        cars = _cars(msg)
        if not st or not cars:
            continue
        label, closed = st
        tag = TIME_TAG.search(msg)
        tag = tag.group(1) if tag else ""
        reason = _reason(msg)
        inc = next((i for i in reversed(incidents)
                    if (tag and i["tag"] == tag)
                    or (reason and i["reason"] == reason and set(cars) & set(i["cars"]))), None)
        if inc is None:
            turn = TURN.search(msg)
            inc = {"cars": cars, "turn": int(turn.group(1)) if turn else None, "reason": reason,
                   "tag": tag, "history": []}
            incidents.append(inc)
        else:
            inc["cars"] = inc["cars"] + [c for c in cars if c not in inc["cars"]]
            inc["tag"] = inc["tag"] or tag
            inc["reason"] = inc["reason"] or reason
        inc.update(status=label, closed=closed, lap=m.get("Lap"))
        inc["history"].append(label)
        if PENALTY.search(msg):
            for c in cars:
                drv(c)["penalties"].append(label)

    for inc in incidents:
        if not inc["closed"]:
            for c in inc["cars"]:
                drv(c)["open"] += 1
    return {"incidents": [{k: v for k, v in i.items() if k != "tag"} for i in incidents], "drivers": drivers}
