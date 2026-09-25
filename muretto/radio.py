"""Team radio trascritti in locale con whisper (whisper.cpp, già installato sul Mac).

Ogni radio nuovo va in coda e si trascrive UNO ALLA VOLTA in un processo a parte
(asyncio.create_subprocess_exec): il server non si blocca mai. Il testo finisce
in una cache su disco per sessione, così a un riavvio non si rifà niente. Se
whisper o il modello mancano la funzione si spegne da sola e i radio restano come
prima (solo audio).

A Monza 2026 (25 radio) una clip richiede ~2-4 s col modello medium su M1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from .feed import STATIC_BASE, cache_dir

log = logging.getLogger(__name__)

MODEL = Path(os.environ.get("MURETTO_WHISPER_MODEL", str(Path.home() / ".whisper-models" / "ggml-medium.bin")))
# il gergo F1 nel prompt aiuta whisper a scrivere "stewards" invece di "sewers"
PROMPT = ("Formula 1 team radio. Box box, box this lap. Stewards, penalty, undercut, overcut, DRS, inters, "
          "wets, softs, mediums, hards, tyres, degradation, push, copy, safety car, VSC, gap, delta, plan B.")
TIMEOUT = 90  # secondi massimi per una clip

# errori ricorrenti di whisper sul gergo F1 → forma giusta (parole intere, senza maiuscole)
FIXES = {
    r"sewers?": "stewards", r"stuarts?": "stewards", r"stew arts": "stewards",
    r"in tres": "inters", r"inter's": "inters", r"d r s": "DRS", r"v s c": "VSC",
    r"tires": "tyres", r"tire": "tyre", r"under cut": "undercut", r"over cut": "overcut",
    r"bocks": "box", r"books box": "box box",
}
_FIX_RE = [(re.compile(rf"\b{k}\b", re.I), v) for k, v in FIXES.items()]

# parole "calde" con una spiegazione in italiano per chi non mastica il gergo
HOT = {
    "box": "rientro ai box", "pit": "sosta ai box", "rain": "pioggia", "damage": "danni alla macchina",
    "puncture": "foratura", "penalty": "penalità", "stewards": "commissari di gara", "problem": "un problema",
    "investigation": "indagine dei commissari", "black and white": "bandiera bianco-nera (ammonizione)",
    "engine": "motore", "brakes": "freni", "fire": "fuoco", "safety car": "safety car",
    "undercut": "fermarsi prima dell'avversario per passarlo con la gomma nuova",
    "overcut": "restare fuori più a lungo dell'avversario",
    "inters": "gomme intermedie (pioggia leggera)", "plan b": "strategia di riserva",
    "retire": "ritiro", "stop the car": "fermare la macchina",
}
_HOT_RE = [(k, re.compile(rf"\b{re.escape(k)}\b", re.I)) for k in HOT]

def clean_text(raw: str | None) -> str | None:
    """Testo pulito e corretto, oppure None se è solo rumore o un'allucinazione del prompt."""
    t = " ".join((raw or "").split())
    # whisper scrive tra parentesi i suoni che non sono parlato: "(beeps)", "(trimmer buzzing)",
    # "(alarm blaring)", "[BLANK_AUDIO]" (a Monza 3 clip su 25 erano solo questo)
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    t = " ".join(t.split()).strip(" .,-")
    if not t or len(re.findall(r"[A-Za-z]", t)) < 3:
        return None
    # con clip mute whisper a volte ripete il prompt: non è un radio
    words = set(re.findall(r"[a-z]+", t.lower()))
    prompt_words = set(re.findall(r"[a-z]+", PROMPT.lower()))
    if len(words) >= 4 and len(words - prompt_words) <= 1:
        return None
    for rx, fix in _FIX_RE:
        t = rx.sub(fix, t)
    return t


def hot_words(text: str | None) -> list[str]:
    """Parole calde nel testo, nell'ordine dell'elenco (per evidenziarle e per il filtro "Caldi")."""
    if not text:
        return []
    return [k for k, rx in _HOT_RE if rx.search(text)]


def recorded_utc(path: str, gmt_offset: str | None, utc: str | None = None) -> float | None:
    """Istante della registrazione (epoch UTC).

    Il nome del file (``PER_11_20260925_133212.mp3``) ha l'ora LOCALE del circuito: meno
    GmtOffset fa UTC. Il campo Utc della F1 arriva in ritardo (a Monza mediana 21 s, fino a
    51 s, e un caso con 576 s): si usa solo se il nome del file non si legge, togliendo 20 s."""
    m = re.search(r"_(\d{8})_(\d{6})\.mp3$", path or "")
    off = _offset(gmt_offset)
    if m and off is not None:
        local = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return (local - off).timestamp()
    t = _utc(utc)
    return t - 20 if t is not None else None


def _offset(s: str | None) -> timedelta | None:
    m = re.match(r"^(-?)(\d{1,2}):(\d{2}):(\d{2})$", (s or "").strip())
    if not m:
        return None
    d = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)), seconds=int(m.group(4)))
    return -d if m.group(1) else d


def _utc(s: str | None) -> float | None:
    try:
        base, _, frac = (s or "").rstrip("Z").partition(".")
        return datetime.fromisoformat(base).replace(tzinfo=timezone.utc).timestamp() + (float("0." + frac) if frac else 0.0)
    except ValueError:
        return None


def lap_at(laps: list, feed_ts: float) -> int | None:
    """Giro che il pilota stava facendo all'istante ``feed_ts`` (tempo del feed).

    ``laps`` sono i giri chiusi (Lap con ``lap`` = numero, ``ts`` = quando è finito): durante il
    giro n il pilota ha chiuso n-1 giri, quindi è il primo giro che finisce dopo quell'istante."""
    if not laps:
        return None
    for lap in laps:
        if lap.ts >= feed_ts:
            return lap.lap
    return laps[-1].lap + 1


class Transcriber:
    """Coda di trascrizione. ``results`` è letto dallo stato (snapshot) ad ogni tick:
    path → {"text": str|None, "noise": bool} quando fatto; assente finché è in coda."""

    def __init__(self):
        self.binary = shutil.which("whisper-cli") or ("/opt/homebrew/bin/whisper-cli" if Path("/opt/homebrew/bin/whisper-cli").exists() else None)
        self.enabled = bool(self.binary) and MODEL.exists()
        if not self.enabled:
            log.info("trascrizione radio spenta: whisper-cli o il modello %s non ci sono", MODEL)
        self.results: dict[str, dict] = {}
        self.queued: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._cache_file: Path | None = None

    def use_session(self, session_path: str | None):
        """Cache della sessione: ~/.cache/muretto/<Path>/radio_text.json."""
        if not session_path:
            return
        f = cache_dir() / session_path.strip("/") / "radio_text.json"
        if f == self._cache_file:
            return
        self._cache_file = f
        try:
            self.results.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            pass

    def enqueue(self, path: str):
        if self.enabled and path not in self.results and path not in self.queued:
            self.queued.add(path)
            self._queue.put_nowait(path)

    async def run(self):
        """Lavoratore: una clip alla volta, per sempre."""
        if not self.enabled:
            return
        async with aiohttp.ClientSession() as http:
            while True:
                path = await self._queue.get()
                try:
                    text = await self._transcribe(http, path)
                    self.results[path] = {"text": text, "noise": text is None}
                    self._save()
                except Exception as e:  # noqa: BLE001 - una clip storta non ferma le altre
                    log.warning("radio %s non trascritto: %s", path, e)
                    self.results[path] = {"text": None, "noise": False, "error": True}
                finally:
                    self.queued.discard(path)

    async def _transcribe(self, http: aiohttp.ClientSession, path: str) -> str | None:
        async with http.get(STATIC_BASE + path, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            data = await r.read()
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            tmp.write(data)
            tmp.flush()
            proc = await asyncio.create_subprocess_exec(
                self.binary, "-m", str(MODEL), "-f", tmp.name, "-l", "en", "-np", "-nt", "--prompt", PROMPT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                raise
        return clean_text(out.decode("utf-8", "replace"))

    def _save(self):
        if not self._cache_file:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(json.dumps(self.results, ensure_ascii=False))
        except OSError as e:
            log.warning("cache dei radio non salvata: %s", e)
