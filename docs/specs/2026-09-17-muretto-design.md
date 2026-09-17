# Muretto — live timing F1 a budget zero

Data: 2026-09-17

## Scopo

Dashboard locale che mostra in tempo reale (circa 3 s dopo l'evento) i dati
pubblici del live timing F1: tabellone con gap e gomme, finestra pit,
telemetria dei piloti, radio e direzione gara. Stessa pagina in modalità
replay per rivedere una sessione passata giro per giro.

## Fonte dati

`livetiming.formula1.com` espone due cose, entrambe senza chiave:

- **Live**: hub SignalR Core su `/signalrcore` (negotiate `POST
  ?negotiateVersion=1`, WebSocket `wss://…/signalrcore?id=<token>`,
  handshake `{"protocol":"json","version":1}`, invocazione `Subscribe`
  con la lista dei canali). Le risposte sono frame JSON separati da
  `\x1e`; il tipo 3 è la risposta alla Subscribe (snapshot iniziale), il
  tipo 1 con target `feed` porta `[topic, dati, timestamp]`.
- **Archivio**: per ogni sessione `static/<Path>/<Topic>.jsonStream`, un
  file di righe `HH:MM:SS.mmm{json}` con gli **stessi messaggi** del feed
  live, più `SessionInfo.json` e l'indice `static/<anno>/Index.json`.

I canali `.z` (`CarData.z`, `Position.z`) sono stringhe base64 di JSON
compresso deflate raw. I messaggi sono delta: dizionari da fondere
ricorsivamente, liste rappresentate come dizionari indice→valore.

Uso non commerciale (termini F1). Il formato può cambiare senza preavviso:
si collauda nelle prove libere del giovedì.

## Architettura

```
muretto/
  feed.py       parse riga jsonStream, inflate .z, LiveFeed (SignalR), ArchiveFeed (replay)
  state.py      RaceState: merge dei delta, storia giri, telemetria recente, viste JSON
  strategy.py   pit loss, posizione d'uscita, degrado gomme, undercut  (funzioni pure)
  server.py     aiohttp: statici + WebSocket /ws, loop di broadcast
  __main__.py   CLI: live | replay | sessions
web/
  index.html app.js style.css vendor/uPlot
tests/
```

Flusso: `Feed` produce `(topic, data, ts)` → `RaceState.apply` → il server
ogni 500 ms invia ai client un messaggio `state` (vista compatta) e, man
mano che arrivano, messaggi `car` e `pos` (telemetria e GPS) per i grafici.
Il replay usa lo stesso `RaceState` e lo stesso server; cambia solo il feed.

## Schermo

1. **Tabellone**: posizione, pilota (colore team), gap leader, intervallo,
   ultimo giro, miglior giro, settori con mini-settori colorati, gomma +
   età, soste, stato (box, out lap, ritirato). Banner stato pista
   (verde / giallo / SC / VSC / rosso), giro N/M, meteo, clock.
2. **Muretto**: per il team a fuoco (default Ferrari) — se entra ora esce
   in P?, dietro/davanti a chi, pit loss usata (osservata dal feed o da
   tabella), degrado stimato del stint (s/giro), minaccia undercut.
   Barre dei stint di tutti.
3. **Telemetria**: fino a 3 piloti scelti, velocità / gas / freno / marcia /
   DRS negli ultimi 60 s, mappa con le posizioni GPS.
4. **Radio + direzione gara**: lista dei team radio con player audio
   (URL statico F1), messaggi della direzione gara, bandiere.

## Errori

Feed live: riconnessione con backoff; se arriva un `SessionInfo` nuovo si
riparte da zero (nuova sessione). Messaggi non parsabili: log e avanti.
Client WebSocket: ricevono lo stato completo alla connessione.

## Test

pytest su: parse righe archivio (BOM, `.z`), merge dei delta (dict,
liste-come-dict, estensione), storia giri, strategia (gap, uscita,
degrado). Il replay di Monza 2026 è il collaudo end-to-end.
