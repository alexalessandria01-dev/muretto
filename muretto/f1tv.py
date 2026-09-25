"""Account F1 TV (facoltativo).

In diretta la F1 manda telemetria (``CarData.z``) e GPS (``Position.z``) solo a chi
si collega con il token di un abbonamento F1 TV Access/Pro/Premium. Senza token il
muretto funziona lo stesso: nasconde la telemetria e stima la mappa dai minisettori.

Il token è il ``subscriptionToken`` (un JWT) che il sito della F1 tiene nel cookie
``login-session`` dopo il login. Si può incollare il valore del cookie così com'è
(JSON codificato nell'URL) oppure il solo token. Resta sul Mac, in
``~/.config/muretto/f1tv.json``, e non viene mai rimandato alla pagina.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import unquote

PRODUCTS = ("F1 TV Access", "F1 TV Pro", "F1 TV Premium")


def config_file() -> Path:
    return Path.home() / ".config" / "muretto" / "f1tv.json"


def parse_token(raw: str) -> str | None:
    """Dal valore del cookie ``login-session`` (o dal token incollato da solo) al JWT."""
    raw = (raw or "").strip().strip('"')
    if raw.lower().startswith("login-session="):
        raw = raw.split("=", 1)[1]
    for text in (raw, unquote(raw)):
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            tok = (data.get("data") or {}).get("subscriptionToken") or data.get("subscriptionToken")
            return tok if tok and _payload(tok) is not None else None
    return raw if _payload(raw) is not None else None


def _payload(token: str) -> dict | None:
    """Contenuto del JWT, senza verificarne la firma (serve solo a leggere scadenza e prodotto)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        body = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def info(token: str | None, now: float | None = None) -> dict:
    """Stato dell'account per la pagina: mai il token, solo prodotto e scadenza."""
    if not token:
        return {"connected": False}
    p = _payload(token) or {}
    exp = p.get("exp")
    now = time.time() if now is None else now
    product = p.get("SubscribedProduct") or ""
    problem = None
    if exp and exp < now:
        problem = "scaduto: rifai il login sul sito F1 e incolla il nuovo"
    elif (p.get("SubscriptionStatus") or "").lower() not in ("", "active"):
        problem = "abbonamento non attivo"
    elif product and product not in PRODUCTS:
        problem = f"{product} non include la telemetria in diretta"
    return {"connected": problem is None, "product": product or None, "expires": exp, "problem": problem}


def load() -> str | None:
    try:
        return json.loads(config_file().read_text()).get("token") or None
    except (OSError, ValueError):
        return None


def save(token: str | None) -> None:
    f = config_file()
    if not token:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"token": token}))
    f.chmod(0o600)
