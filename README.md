---
title: Muretto
emoji: 🏎️
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Muretto

Live timing F1 a budget zero: tabellone, muretto (finestra pit, degrado, undercut),
battaglia del pilota seguito, telemetria, mappa, team radio e direzione gara.
Legge il feed pubblico di `livetiming.formula1.com` (lo stesso della app ufficiale),
in diretta o in replay di qualsiasi sessione dal 2018. Uso personale, non commerciale.

## Avvio

```bash
cd ~/Projects/muretto
python3 -m muretto live                         # durante una sessione (FP, qualifica, gara)
python3 -m muretto replay 2026 italian race     # replay di Monza 2026
python3 -m muretto replay 2026 italian race --speed 3 --start 01:36:00
python3 -m muretto sessions 2026                # elenco sessioni in archivio
```

Poi apri **http://localhost:8765** (dal Mac) o `http://<ip-del-mac>:8765` da iPad/TV
sulla stessa rete. Dipendenza: `aiohttp` (`pip install aiohttp`).

Le sessioni in archivio si scaricano una volta in `~/.cache/muretto/` (~25 MB a gara).

## La pagina

- **Barra impostazioni**: pilota da seguire, ritardo TV (il feed è avanti di 3-30 s
  rispetto alla diretta: metti i secondi per allinearlo ed evitare spoiler), pit loss
  manuale, pannelli da mostrare, marcia/velocità in tabella, radio automatici,
  campanello ai messaggi della direzione gara. Tutto resta salvato nel browser.
- **Tabellone**: posizione, ▲▼ rispetto alla griglia, DRS, box/out lap, gap,
  intervallo (verde = si avvicina), ultimo e miglior giro (viola = migliore assoluto),
  settori con mini-settori, gomma con età, soste, barra dei stint. ★ = preferito.
  In qualifica: zona eliminazione in rosso, distacchi della fase corrente.
- **Battaglia**: chi ha davanti e dietro il pilota seguito, intervalli con tendenza
  (s/giro negli ultimi 3 giri), DRS in portata (< 1 s), dove rientrerebbe se entrasse
  ora e sotto Safety Car.
- **Muretto**: per seguito e preferiti — posizione d'uscita dai box, trend gomma
  (pendenza dei tempi negli ultimi 10 giri puliti), minaccia undercut da dietro.
- **Gap**: grafico giro per giro del gap dal leader (seguito, vicini, preferiti).
- **Telemetria**: velocità, gas/freno, marcia degli ultimi 60 s per 3 piloti.
- **Mappa**: tracciato reale (MultiViewer) con numeri di curva, settori gialli dalla
  direzione gara, posizioni GPS; senza tracciato usa la traccia dei GPS.
- **Team radio**: player per ogni messaggio, autoplay dei nuovi (solo del seguito e
  dei preferiti, se ne hai scelti); **Direzione gara**: messaggi con colore bandiera.

## Dati

Pit loss per circuito (normale / SC / VSC) da `api.multiviewer.app`; se non risponde
si usa una tabella interna. Il tempo in pit lane osservato nella sessione è mostrato
come riferimento. Il formato del feed F1 può cambiare senza preavviso: collaudare
nelle prove libere del giovedì con `live`.

## Sviluppo

```bash
python3 -m pytest -q
```

Struttura in `docs/specs/2026-09-17-muretto-design.md`.
