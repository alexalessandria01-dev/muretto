/* Muretto — pagina live.
 * Dal server arrivano: state (ogni 0,5 s), car_history (alla connessione), car / pos (campioni nuovi).
 * Tutte le preferenze (pilota seguito, preferiti, pannelli, ritardo) restano nel browser.
 */
(() => {
  const $ = (s) => document.querySelector(s);
  const KEEP = 600, WINDOW = 60, TRAIL = 2500;

  let state = null, ws, mapData = null, mapPath = null;
  const tel = {}, trail = {}, lastPos = {};

  // ------------------------------------------------------------ preferenze
  const store = (k, def) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? def; } catch { return def; } };
  const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
  const prefs = {
    follow: store("follow", ""), favs: store("favs", []), telDrivers: store("telDrivers", []),
    panels: store("panels", {}), metrics: store("metrics", false), autoplay: store("autoplay", true), chime: store("chime", false),
    delay: store("delay", 0), pitLoss: store("pitLoss", null), tab: store("tab", "board"),
    wallAll: store("wallAll", false), mobile: store("mobile", "auto"), rcFilter: store("rcFilter", "all"), radioFilter: store("radioFilter", "all"),
  };
  /** Pit loss in uso: quella scelta dall'utente, altrimenti quella del server (già adattata a SC/VSC). */
  const pitLossNow = () => prefs.pitLoss ?? state.pit_loss.value;
  const pitLossNormal = () => prefs.pitLoss ?? state.pit_loss.normal;
  const isFav = (n) => prefs.favs.includes(n);

  // ------------------------------------------------------------ websocket
  let lastMsgAt = Date.now(), reconnectTimer = null;
  function connect() {
    clearTimeout(reconnectTimer);
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?delay=${prefs.delay || 0}`);
    ws.onopen = () => { $("#conn").classList.add("on"); lastMsgAt = Date.now(); };
    ws.onclose = () => { $("#conn").classList.remove("on"); reconnectTimer = setTimeout(connect, 2000); };
    ws.onmessage = (ev) => {
      lastMsgAt = Date.now();
      const m = JSON.parse(ev.data);
      if (m.type === "state") { state = m; render(); }
      else if (m.type === "car_history") { for (const [n, s] of Object.entries(m.drivers)) tel[n] = s.slice(-KEEP); }
      else if (m.type === "car") { for (const s of m.samples) { (tel[s.n] ||= []).push(s); if (tel[s.n].length > KEEP) tel[s.n].splice(0, tel[s.n].length - KEEP); } }
      else if (m.type === "pos") {
        for (const p of m.samples) {
          if (p.x == null || (p.x === 0 && p.y === 0)) continue;
          lastPos[p.n] = p;
          const tr = (trail[p.n] ||= []); tr.push([p.x, p.y]); if (tr.length > TRAIL) tr.splice(0, tr.length - TRAIL);
        }
      }
    };
  }
  const send = (o) => { if (ws?.readyState === 1) ws.send(JSON.stringify(o)); };

  /** Dati fermi: lo stato arriva ogni mezzo secondo, se tace da 5 s qualcosa non va (WiFi, Mac in
   *  stop, telefono che ha sospeso la pagina). Si dice, e da 10 s ci si ricollega da capo. */
  function reconnectNow() {
    if (ws && ws.readyState <= 1) { ws.onclose = null; ws.close(); }
    $("#conn").classList.remove("on");
    connect();
  }
  setInterval(() => {
    const quiet = (Date.now() - lastMsgAt) / 1000, el = $("#stale");
    el.hidden = quiet < 5 || !state;
    if (!el.hidden) el.textContent = `Dati fermi da ${Math.round(quiet)} s: mi sto ricollegando…`;
    if (quiet >= 10 && ws?.readyState === 1) reconnectNow();
  }, 1000);
  // schermo riacceso: il telefono può aver sospeso la pagina, si riparte subito senza aspettare
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (!ws || ws.readyState !== 1 || Date.now() - lastMsgAt > 3000)) reconnectNow();
  });

  // ------------------------------------------------------------ helpers
  const fmtClock = (s) => { if (s == null) return "–"; s = Math.floor(s); return [s / 3600, s / 60 % 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, "0")).join(":"); };
  const localTime = (utc) => { const d = new Date(utc); return isNaN(d) ? "" : d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const byNum = (n) => state?.drivers.find((d) => d.num === n);
  const tlaOf = (n) => byNum(n)?.tla || n;
  const colourOf = (n) => byNum(n)?.colour || "#888";
  const TRACK_LABEL = { green: "VERDE", yellow: "GIALLA", sc: "SAFETY CAR", vsc: "VSC", vsc_ending: "VSC FINISCE", red: "ROSSA" };
  const isRace = () => state?.session.type === "Race";
  /** Bandiera rossa: le macchine sono ferme in pit lane e le gomme si cambiano gratis, la finestra box non ha senso. */
  const RED_PIT = "bandiera rossa: cambio gomme gratis";
  const isRedFlag = () => state?.session.track === "red";
  /** Soste, con quelle fatte a gara sospesa (gratis, ma la F1 le conta). */
  const stopsTxt = (d) => `${d.stops}${d.free_stops ? ` (${d.free_stops} con la rossa)` : ""}`;
  /** L'avviso "una sola mescola" serve da un terzo di gara in poi (al via ce l'hanno tutti), e mai nella Sprint. */
  const mixDue = () => isRace() && state.session.name !== "Sprint" && state.session.lap && state.session.total_laps && state.session.lap >= state.session.total_laps / 3;
  const drsState = (v) => (v == null ? "" : v > 9 ? "on" : v === 8 ? "possible" : "off");
  /** Modalità telefono. Alcuni telefoni (es. col "sito desktop" attivo) dichiarano una
   *  larghezza da computer: per questo si può forzare a mano dalle impostazioni. */
  const mq = window.matchMedia("(max-width: 820px)");
  const isMobile = () => document.body.classList.contains("m");
  function applyMobileMode() {
    const on = prefs.mobile === "on" ? true : prefs.mobile === "off" ? false : mq.matches;
    if (on === isMobile()) return false;
    document.body.classList.toggle("m", on);
    return true;
  }
  /** Un pannello nascosto dal CSS ha larghezza 0: i grafici non vanno ridisegnati. */
  const isVisible = (el) => !!el && el.offsetParent !== null && el.clientWidth > 0;

  /** Tempo in pit lane: secondi per una sosta, minuti quando è una sospensione (bandiera rossa). */
  function fmtPitTime(d) {
    const s = parseFloat(d);
    if (isNaN(s)) return esc(d);
    return s < 90 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")} min`;
  }
  /** Direzione del vento in gradi → freccia che indica dove soffia. */
  const windArrow = (deg) => "↑↗→↘↓↙←↖"[Math.round((((+deg || 0) % 360) + 360) % 360 / 45) % 8];
  /** Le quattro rilevazioni di velocità, col viola di chi ha il record assoluto. */
  function speedCells(d) {
    const s = d.speeds; if (!s) return "";
    const one = (k, label) => {
      const v = s[k] || {};
      return `<span class="sp ${v.of ? "of" : v.pf ? "pf" : ""}"><i>${label}</i>${esc(v.v) || "–"}</span>`;
    };
    return `<span class="speeds">${one("i1", "I1")}${one("i2", "I2")}${one("st", "TRAP")}${one("fl", "TRAG")}</span>`;
  }

  function setOptions(sel, items, value) {
    sel.innerHTML = items.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("");
    sel.value = value ?? "";
    if (sel.selectedIndex < 0 && items.length) sel.selectedIndex = 0;
  }

  /** Se `num` entra ai box adesso con `loss` secondi di perdita: posizione d'uscita e vicini. */
  function pitExit(num, loss) {
    const me = byNum(num); if (!me || me.gap_s == null) return null;
    const field = state.drivers.filter((d) => d.num !== num && !d.retired && !d.stopped && d.gap_s != null).map((d) => [d.num, d.gap_s]).sort((a, b) => a[1] - b[1]);
    const exitGap = me.gap_s + loss;
    const ahead = field.filter(([, g]) => g < exitGap), behind = field.filter(([, g]) => g >= exitGap);
    const now = 1 + field.filter(([, g]) => g < me.gap_s).length;
    return { pos: 1 + ahead.length, lost: 1 + ahead.length - now, behind: ahead.at(-1) ? [ahead.at(-1)[0], (exitGap - ahead.at(-1)[1]).toFixed(1)] : null, aheadOf: behind[0] ? [behind[0][0], (behind[0][1] - exitGap).toFixed(1)] : null };
  }

  // ------------------------------------------------------------ battaglie (calcoli sul server, strategy.py)
  /** Trenino (3+ auto entro 1 s) di cui fa parte un pilota, se c'è. */
  const trainOf = (num) => (state.trains || []).find((t) => t.includes(num)) || null;
  /** "ANT recupera 0,7 s/giro su RUS → a questo ritmo sotto 1 s al giro ~48" */
  function chaseTxt(d) {
    const c = d?.chase; if (!c) return "";
    const end = c.in_time === false ? "a questo ritmo non ce la fa prima della fine" : `a questo ritmo sotto 1 s al giro ~${c.lap}`;
    return `<b>${esc(d.tla)}</b> recupera ${c.rate.toFixed(2)} s/giro su <b>${esc(c.on)}</b> → ${end}`;
  }
  const stuckTxt = (d) => d?.stuck ? `<b>${esc(d.tla)}</b> <span class="warn">BLOCCATO</span> in scia a <b>${esc(d.stuck.behind)}</b> da ${d.stuck.laps} giri` : "";
  /** Righe di battaglia per il pilota seguito: chi lo prende, chi prende lui, chi è bloccato. */
  function battleLines(me) {
    if (!isRace() || !me) return "";
    if ((me.lap_history || []).length < 4) return '<div class="blines"><span class="hint">servono 4 giri di dati per le previsioni di battaglia</span></div>';
    const behind = state.drivers.find((d) => d.pos === me.pos + 1);
    const lines = [chaseTxt(me), stuckTxt(me), behind?.chase?.on === me.tla ? chaseTxt(behind) : "", behind?.stuck ? stuckTxt(behind) : ""].filter(Boolean);
    return lines.length ? `<div class="blines">${lines.map((l) => `<div>${l}</div>`).join("")}</div>` : "";
  }
  /** Tabellone: trenino (sul primo), "lo prende al giro ~N", "in scia da N giri". */
  function battleBadges(d, tr) {
    let out = tr && tr[0] === d.num ? `<span class="flagb trainb" title="${tr.length} auto entro 1 s l'una dall'altra">TRENINO ${tr.length}</span>` : "";
    if (d.chase) out += `<span class="flagb chase" title="recupera ${d.chase.rate.toFixed(2)} s/giro su ${esc(d.chase.on)}: a questo ritmo">${d.chase.in_time === false ? "NON CE LA FA" : `→ G${d.chase.lap}`}</span>`;
    if (d.stuck) out += `<span class="flagb stuck" title="bloccato in scia a ${esc(d.stuck.behind)}">SCIA ${d.stuck.laps}</span>`;
    return out;
  }
  /** Aria all'uscita dai box: dietro un trenino o a meno di 1 s c'è aria sporca. */
  function airTxt(e) {
    if (!e || !e.behind) return "";
    const [num, gap] = [e.behind[0], parseFloat(e.behind[1])], t = trainOf(num);
    if (t && gap < 1.5) return ` · <span class="bad">esci dietro al trenino ${t.map(tlaOf).join("-")}: aria sporca</span>`;
    if (gap < 1.0) return ` · <span class="warn">aria sporca dietro a ${esc(tlaOf(num))}</span>`;
    return ' · <span class="good">aria libera</span>';
  }

  // ------------------------------------------------------------ render
  function render() {
    const s = state.session, w = state.weather;
    $("#session-title").textContent = [s.meeting, s.name, s.circuit].filter(Boolean).join(" · ");
    const ts = $("#track-status"); ts.className = `pill ${s.track}`; ts.textContent = TRACK_LABEL[s.track] || s.track_msg;
    $("#lap").textContent = isRace() ? (s.lap ? `${s.lap}${s.total_laps ? " / " + s.total_laps : ""}` : "–") : (s.part ? `${s.name} · Q${s.part}` : s.name || "–");
    // a tempo scaduto le macchine chiudono l'ultimo giro: "00:00:00" con le auto in pista confonde
    // in qualifica la F1 manda "Finished" anche alla fine di Q1 e Q2 (a Monza a 1883 s, poi riparte)
    $("#remaining").textContent = s.status === "Finished" && s.part && s.part < 3 ? `fine Q${s.part}`
      : ["Finished", "Finalised", "Ends"].includes(s.status) ? "finita"
      : s.status === "Aborted" ? "sospesa" : s.remaining || "–";
    $("#w-air").textContent = w.air ? `${w.air}°` : "–";
    $("#w-track").textContent = w.track ? `${w.track}°` : "–";
    $("#w-wind").textContent = w.wind ? `${w.wind} m/s${w.wind_dir ? " " + windArrow(w.wind_dir) : ""}` : "–";
    $("#w-hum").textContent = w.humidity ? `${w.humidity} %` : "–";
    $("#w-press").textContent = w.pressure ? `${w.pressure} mb` : "–";
    $("#w-rain").textContent = w.rain == null ? "–" : (String(w.rain) === "1" ? "SÌ" : "no");
    $("#replay-box").hidden = s.replay_position == null;
    $("#replay-pos").textContent = fmtClock(s.replay_position);
    document.body.classList.toggle("show-metrics", prefs.metrics);

    renderControls();
    renderHero();
    renderBoard();
    renderBattle();
    renderWall();
    renderGaps();
    renderRadio();
    renderRaceControl();
    renderTelemetry();
    renderMap();
    renderPerf();
    renderChamp();
    renderCmp();
  }

  function renderControls() {
    const drivers = state.drivers.map((d) => [d.num, `${d.tla} · ${d.team}`]);
    const fs = $("#follow");
    if (fs.options.length !== drivers.length + 1) setOptions(fs, [["", "— nessuno —"], ...drivers], prefs.follow);
    document.querySelectorAll(".tel-driver").forEach((sel, i) => {
      if (sel.options.length !== drivers.length + 1) setOptions(sel, [["", "—"], ...drivers], prefs.telDrivers[i] ?? defaultTelDriver(i));
    });
    const pl = $("#pit-loss"), o = state.pit_loss;
    if (document.activeElement !== pl) pl.value = pitLossNormal();
    $("#pit-loss-hint").textContent = `${prefs.pitLoss != null ? "manuale" : o.source}${o.circuit ? " · " + o.circuit : ""} · SC ${o.sc} · VSC ${o.vsc}` + (o.observed_median ? ` · pit lane osservata ${o.observed_median} s` : "");
    const dl = $("#delay"); if (document.activeElement !== dl) dl.value = prefs.delay;
    for (const cb of document.querySelectorAll("[data-panel]")) { const on = prefs.panels[cb.dataset.panel] ?? true; cb.checked = on; $("#" + cb.dataset.panel).hidden = !on; }
    $("#opt-metrics").checked = prefs.metrics; $("#opt-autoplay").checked = prefs.autoplay; $("#opt-chime").checked = prefs.chime;
    $("#opt-wall-all").checked = prefs.wallAll;
    $("#opt-mobile").value = prefs.mobile;
    // le schede seguono i pannelli scelti; se sparisce quella aperta si torna al tabellone
    for (const b of $("#tabbar").children) {
      if (b.dataset.tab === "board") continue;
      b.hidden = (prefs.panels[b.dataset.tab] ?? true) === false;
    }
    if (prefs.tab !== "board" && (prefs.panels[prefs.tab] ?? true) === false) setTab("board");
  }

  function defaultTelDriver(slot) {
    const f = prefs.follow || state.drivers[0]?.num;
    const me = byNum(f), ahead = me && state.drivers.find((d) => d.pos === me.pos - 1), behind = me && state.drivers.find((d) => d.pos === me.pos + 1);
    return [f, ahead?.num || behind?.num, behind?.num !== ahead?.num ? behind?.num : ""][slot] || "";
  }

  /** Scheda del pilota seguito, in cima alla pagina su telefono. Riassume quello che i
   *  pannelli Battaglia e Muretto dicono in grande, per non doverli aprire durante la gara. */
  function renderHero() {
    const el = $("#hero");
    if (!isMobile()) { el.innerHTML = ""; return; }
    const me = byNum(prefs.follow);
    if (!me) { el.innerHTML = '<div class="empty">Scegli un pilota da seguire in <b>Impostazioni</b> per vederlo qui.</div>'; return; }

    const ahead = state.drivers.find((d) => d.pos === me.pos - 1), behind = state.drivers.find((d) => d.pos === me.pos + 1);
    const gained = me.gained == null || !isRace() ? ""
      : me.gained > 0 ? `<span class="gained up">▲${me.gained}</span>` : me.gained < 0 ? `<span class="gained down">▼${-me.gained}</span>` : "";
    const st = me.retired ? '<span class="status">RIT</span>' : me.stopped ? '<span class="status">FERMO</span>'
      : me.inpit ? '<span class="status pit">BOX</span>' : me.pitout ? '<span class="status out">OUT</span>' : "";
    const cell = (k, v) => `<div class="h-cell"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    // "leader" solo a chi è davvero primo: il gap è vuoto anche per i ritirati e per chi è a giri
    const gapTxt = esc(me.gap) || (me.pos === 1 ? "leader" : "–");
    const cells = isRace()
      ? cell("Gap dal leader", gapTxt) + cell("Intervallo", esc(me.interval) || "–")
        + cell("Ultimo giro", esc(me.last) || "–") + cell("Miglior giro", esc(me.best) || "–")
      : cell("Ultimo giro", esc(me.last) || "–") + cell(state.session.part ? `Miglior Q${state.session.part}` : "Miglior giro", esc(me.part_best || me.best) || "–")
        + cell("Distacco", gapTxt)
        + (state.session.cut ? cell(`Taglio Q${state.session.part} (P${state.session.cut})`, cutTxt(me) || "–") : cell("Velocità trap", `${esc(me.speed_trap) || "–"}`));

    const cr = me.compounds;
    const notes = flyBadge(me) + stewardBadges(me) + (mixDue() && cr && !cr.ok && !cr.wet && !me.retired ? '<span class="flagb mix">UNA SOLA MESCOLA</span>' : "")
      + ((me.pit_stops || []).length ? `<span class="hint">soste: ${me.pit_stops.map((p) => `G${esc(p.lap)} ${esc(p.stop)} s`).join(" · ")}</span>` : "");
    let pit = "";
    if (isRace() && !me.retired) {
      const e = pitExit(me.num, pitLossNow());
      pit = isRedFlag() ? `<div class="h-pit">${RED_PIT}</div>` : `<div class="h-pit">Se entra ora (${pitLossNow()} s): ${!e ? "—" : `esce <b>P${e.pos}</b>${e.lost > 0 ? ` <span class="warn">(−${e.lost})</span>` : ' <span class="good">(nessuna posizione persa)</span>'}${airTxt(e)}`}</div>`;
    }

    el.innerHTML = `
      <div class="h-top" style="border-left:0">
        <span class="h-pos" style="color:${me.colour}">${me.pos}</span>
        <span class="h-id"><span class="h-tla">${esc(me.tla)}${gained}${st}</span><span class="h-name">${esc(me.name)}</span></span>
        <span class="h-tyre"><span class="tyre"><b class="${me.compound}"></b>${me.age} giri</span>
          <span class="hint">griglia ${me.grid ?? "–"}</span></span>
      </div>
      <div class="h-grid">${cells}</div>
      ${notes ? `<div class="h-notes">${notes}</div>` : ""}
      <div class="h-neigh battle">${whoRow(ahead, me, "ahead")}${whoRow(behind, me, "behind")}</div>
      ${battleLines(me)}
      ${pit}`;
  }

  function carCell(d) {
    const c = d.car; if (!c) return "";
    return `<span class="car"><span class="gear">${c.gear ?? ""}</span><span class="spd">${c.speed ?? ""}</span><span class="bars"><i class="th"><b style="width:${c.throttle || 0}%"></b></i><i class="br"><b style="width:${c.brake ? 100 : 0}%"></b></i></span></span>`;
  }

  /** Qualifica: giro lanciato in corso, con tempo previsto (settori fatti + suoi migliori) e verdetto. */
  function flyBadge(d) {
    const f = d.flying; if (!f) return "";
    const cls = f.verdict === "si salva" ? "ok" : f.verdict === "non basta" ? "ko" : f.verdict ? "mid" : "";
    return `<span class="flagb fly ${cls}" title="stima dopo S${f.after}: settori fatti + suoi migliori">▶ ${esc(f.pred)} P${f.pos}${f.verdict ? " · " + esc(f.verdict.toUpperCase()) : ""}</span>`;
  }
  /** Qualifica: quanto margine ha sul taglio (dentro) o quanto gli manca (fuori). */
  const cutTxt = (d) => d.cut_gap == null ? "" : d.cut_gap >= 0
    ? `dentro di ${d.cut_gap.toFixed(3)} s` : `fuori: serve ${d.cut_gap.toFixed(3)} s`;

  /** Penalità, indagini aperte e track limits: quello che un muretto guarda sui commissari. */
  function stewardBadges(d) {
    const s = d.stewards || {}, out = [];
    if (s.penalties?.length) out.push(`<span class="flagb pen" title="${esc(s.penalties.join(", "))}">PEN</span>`);
    if (s.open) out.push('<span class="flagb inv" title="incidente in esame dai commissari">ESAME</span>');
    if (s.track_limits) out.push(`<span class="flagb tl ${s.track_limits >= 4 ? "bad" : s.track_limits >= 3 ? "warn" : ""}" title="giri o tempi cancellati per track limits">TL${s.track_limits}</span>`);
    return out.join("");
  }

  /** Riga aperta sul telefono: il tabellone si ricostruisce ogni mezzo secondo, va tenuta qui. */
  let openNum = "";
  function renderBoard() {
    const part = state.session.part, quali = state.session.type === "Qualifying";
    const rows = state.drivers.map((d) => {
      const st = d.inpit ? '<span class="status pit">BOX</span>' : d.pitout ? '<span class="status out">OUT</span>' : d.retired ? '<span class="status">RIT</span>' : d.stopped ? '<span class="status">FERMO</span>' : d.knocked_out ? '<span class="status">ELIM</span>' : "";
      const drs = isRace() && d.car ? `<span class="drs ${drsState(d.car.drs)}">DRS</span>` : "";
      const gained = d.gained == null || !isRace() ? "" : d.gained > 0 ? `<span class="gained up">▲${d.gained}</span>` : d.gained < 0 ? `<span class="gained down">▼${-d.gained}</span>` : `<span class="gained">–</span>`;
      const sectors = d.sectors.map((s, i) => `<span class="sector"><span class="${s.of || d.best_sector_pos?.[i] === 1 ? "of" : s.pf ? "pf" : ""}">${esc(s.v) || "&nbsp;"}</span><span class="segs">${s.seg.map((x) => `<b class="s${x}"></b>`).join("")}</span></span>`).join("");
      const total = Math.max(1, d.stints.reduce((a, x) => a + (x.laps || 0), 0));
      const stints = d.stints.map((x) => `<b class="${x.compound}" style="width:${(100 * (x.laps || 0) / total).toFixed(1)}%" title="${x.compound} ${x.laps} giri${x.new ? "" : " (usata)"}"></b>`).join("");
      // il taglio lo dice la F1 (NoEntries): con 22 macchine in Q1 passano 16, non 15
      const cut = state.session.cut, danger = quali && part && cut && d.pos > cut;
      const tr = trainOf(d.num), trCls = !tr ? "" : `train${tr[0] === d.num ? " train-first" : ""}${tr.at(-1) === d.num ? " train-last" : ""}`;
      const cls = [d.num === prefs.follow ? "follow" : isFav(d.num) ? "fav" : "", d.retired || d.stopped ? "retired" : "", d.knocked_out ? "out" : "", danger ? "danger" : "", d.num === openNum ? "open" : "", trCls].join(" ");
      return `<tr class="${cls}" data-num="${d.num}">
        <td class="pos">${d.pos}</td>
        <td class="col-drv"><span class="drv"><i style="background:${d.colour}"></i>${esc(d.tla)}</span>${gained}${drs}${st}${stewardBadges(d)}${flyBadge(d)}${battleBadges(d, tr)}</td>
        <td class="star ${isFav(d.num) ? "on" : ""}" title="preferito">★</td>
        <td class="r col-gap">${esc(d.gap) || (d.pos === 1 ? '<span class="leader">LEADER</span>' : "")}</td>
        <td class="r col-int ${d.catching ? "catching" : ""}">${esc(d.interval)}</td>
        <td class="r col-last ${d.last_of ? "of" : d.last_pf ? "pf" : ""}">${esc(d.last)}</td>
        <td class="r col-best">${esc(d.best)}</td>
        <td class="col-sectors"><span class="sectors">${sectors}</span></td>
        <td class="col-tyre"><span class="tyre"><b class="${d.compound}"></b>${d.age}${d.new ? "" : '<span class="used">usata</span>'}</span></td>
        <td class="r col-stops">${stopsTxt(d)}</td>
        <td class="col-stints"><span class="stints">${stints}</span></td>
        <td class="metrics">${carCell(d)}</td>
      </tr>`;
    });
    // linea del taglio in qualifica, subito dopo l'ultimo che passa
    const cut = state.session.cut;
    if (quali && part && cut && state.session.cut_time && cut < rows.length)
      rows.splice(cut, 0, `<tr class="cutrow"><td colspan="12">TAGLIO Q${part} · P${cut} ${esc(state.session.cut_tla)} ${esc(state.session.cut_time)}</td></tr>`);
    $("#leaderboard tbody").innerHTML = rows.join("");
  }

  function whoRow(d, me, role) {
    if (!d) return `<div class="who"><span class="p">–</span><span>${role === "ahead" ? "nessuno davanti: è in testa" : "nessuno dietro"}</span></div>`;
    let trend = "";
    if (role !== "me" && isRace()) {
      const t = role === "ahead" ? me.ahead_trend : d.ahead_trend;  // strategy.interval_trend
      if (t != null) { const closing = role === "ahead" ? t < 0 : t > 0; trend = `<span class="trend ${closing ? "closing" : "opening"}">${closing ? "si avvicina" : "si allontana"} ${Math.abs(t).toFixed(2)} s/giro</span>`; }
    }
    const iv = role === "ahead" ? me.interval : role === "behind" ? d.interval : d.gap || "leader";
    const ivNum = parseFloat(String(iv).replace("+", ""));
    const drsRange = role !== "me" && isRace() && !isNaN(ivNum) && ivNum < 1 ? ' <span class="good">DRS</span>' : "";
    return `<div class="who ${role === "me" ? "me" : ""}" style="border-left-color:${d.colour}">
      <span class="p">P${d.pos}</span><span>${esc(d.tla)}</span>
      <span class="tyre"><b class="${d.compound}"></b>${d.age}</span>
      <span class="hint">${esc(d.last) || ""}</span>
      <span class="iv">${esc(iv)}${drsRange}<br>${trend}</span>
    </div>`;
  }

  function renderBattle() {
    const me = byNum(prefs.follow);
    if (!me) { $("#battle-body").innerHTML = '<div class="hint">scegli un pilota da seguire in alto (Segui)</div>'; $("#battle-hint").textContent = ""; return; }
    const ahead = state.drivers.find((d) => d.pos === me.pos - 1), behind = state.drivers.find((d) => d.pos === me.pos + 1);
    $("#battle-hint").textContent = `${me.name} · griglia ${me.grid ?? "–"} · giro veloce ${me.best || "–"}`;
    let exit = "";
    if (isRace() && !me.retired) {
      const pl = state.pit_loss, n = pitExit(me.num, pitLossNormal()), sc = pitExit(me.num, pl.sc);
      const fmt = (r) => !r ? "–" : `<b>P${r.pos}</b>${r.lost > 0 ? ` (−${r.lost})` : ""}${r.behind ? `, dietro ${tlaOf(r.behind[0])} di ${r.behind[1]} s` : ""}${r.aheadOf ? `, davanti a ${tlaOf(r.aheadOf[0])} di ${r.aheadOf[1]} s` : ""}`;
      const now = pitExit(me.num, pitLossNow());
      exit = isRedFlag() ? `<div class="exit">${RED_PIT}</div>` : `<div class="exit">Se entra ora (${pitLossNow()} s): ${fmt(now)}${airTxt(now)}</div>` + (state.session.track === "green" ? `<div class="exit">Sotto Safety Car (${pl.sc} s): ${fmt(sc)}</div>` : `<div class="exit">In condizioni normali (${pitLossNormal()} s): ${fmt(n)}</div>`);
    }
    $("#battle-body").innerHTML = `<div class="battle">${whoRow(ahead, me, "ahead")}${whoRow(me, me, "me")}${whoRow(behind, me, "behind")}</div>${battleLines(me)}${exit}`;
  }

  function renderWall() {
    const mine = [prefs.follow, ...prefs.favs.filter((n) => n !== prefs.follow)].filter(Boolean);
    const nums = prefs.wallAll ? state.drivers.map((d) => d.num) : mine;
    $("#wall-hint").textContent = prefs.wallAll ? `tutti e ${state.drivers.length} i piloti` : "seguito + preferiti";
    const cards = nums.map(byNum).filter(Boolean).map((d) => {
      const loss = pitLossNow(), e = d.retired ? null : pitExit(d.num, loss), deg = d.deg;
      const exitTxt = !e ? "—" : `<span class="big ${e.lost > 0 ? "warn" : "good"}">P${e.pos}</span> ${e.lost > 0 ? `(−${e.lost})` : "(nessuna posizione persa)"}`
        + (e.behind ? `<br>dietro <b>${esc(tlaOf(e.behind[0]))}</b> di ${e.behind[1]} s` : "<br>in testa")
        + (e.aheadOf ? ` · davanti a <b>${esc(tlaOf(e.aheadOf[0]))}</b> di ${e.aheadOf[1]} s` : "") + (e ? `<br>${airTxt(e).replace(/^ · /, "")}` : "");
      // verdetto del server (strategy.undercut_threat): qui si mostra e basta, senza rifare il conto
      const u = d.undercut;
      // al netto della benzina (stima): tempi piatti vogliono già dire gomma che cala di ~0,05 s/giro
      const degTxt = !deg ? "servono 4 giri puliti" : `<span class="${deg.slope > 0.25 ? "bad" : deg.slope > 0.12 ? "warn" : "good"}">${deg.slope > 0 ? "+" : ""}${deg.slope.toFixed(2)} s/giro</span> su ${deg.laps} giri`
        + `<br><span class="hint">${isRace() ? "al netto della benzina (stima)" : "indicativo: nelle libere si alternano giri lanciati e lenti"}</span>`;
      const uTxt = !u ? "nessuno dietro a portata" : `<b>${esc(u.by)}</b> a ${u.interval.toFixed(1)} s: <span class="${u.risk === "alto" ? "bad" : u.risk === "medio" ? "warn" : "good"}">rischio ${u.risk}</span>`
        + `<br><span class="hint">la gomma nuova vale ~${u.gain_per_lap} s/giro (stima) · gomme ${u.tyre_delta > 0 ? `mie +${u.tyre_delta} giri` : u.tyre_delta < 0 ? `sue +${-u.tyre_delta} giri` : "pari"}</span>`;
      const bs = d.best_speeds || {};
      const bestSpd = ["i1", "i2", "st", "fl"].some((k) => bs[k])
        ? `${bs.i1 || "–"} / ${bs.i2 || "–"} / ${bs.st || "–"} / ${bs.fl || "–"}` : "–";
      const pt = d.pit_time || {}, cr = d.compounds, sw = d.stewards || {};
      const stops = (d.pit_stops || []).map((p) => `G${esc(p.lap)} <b>${esc(p.stop)} s</b> fermo · ${esc(p.lane)} s in pit lane`).join("<br>");
      const swTxt = [sw.track_limits ? `track limits ${sw.track_limits}` : "", ...(sw.penalties || []).map((p) => `<span class="bad">${esc(p)}</span>`),
        sw.open ? `<span class="warn">${sw.open} in esame</span>` : ""].filter(Boolean).join(" · ");
      const mark = d.num === prefs.follow ? "follow" : isFav(d.num) ? "fav" : "";
      return `<div class="card ${mark}" style="border-left-color:${d.colour}">
        <h3>P${d.pos} ${esc(d.tla)} <small>${esc(d.name)}</small> <span class="tyre"><b class="${d.compound}"></b>${d.age} giri</span></h3>
        <div class="row"><span class="k">Ultimo / migliore</span><span class="v">${esc(d.last) || "–"} / ${esc(d.best) || "–"}</span></div>
        <div class="row"><span class="k">Gap / intervallo</span><span class="v">${esc(d.gap) || (d.pos === 1 ? "leader" : "–")} / ${esc(d.interval) || "–"}</span></div>
        <div class="row"><span class="k">Velocità I1/I2/trap/trag</span><span class="v">${speedCells(d)}</span></div>
        <div class="row"><span class="k">Record velocità</span><span class="v">${esc(bestSpd)}</span></div>
        <div class="row"><span class="k">Giri / soste</span><span class="v">${d.laps || 0} / ${stopsTxt(d)}</span></div>
        ${stops ? `<div class="row"><span class="k">Soste</span><span class="v">${stops}</span></div>`
          : pt.duration ? `<div class="row"><span class="k">Tempo in pit lane</span><span class="v">${fmtPitTime(pt.duration)}${pt.lap ? ` (giro ${esc(pt.lap)})` : ""}</span></div>` : ""}
        ${isRace() ? `<div class="row"><span class="k" title="la F1 conta anche i doppiaggi">Sorpassi (con doppiaggi)</span><span class="v">${d.overtakes || 0}</span></div>` : ""}
        ${isRace() && cr && !d.retired ? `<div class="row"><span class="k">Mescole usate</span><span class="v">${esc(cr.used.join(", ") || "–")}${cr.wet ? " · regola sospesa (pioggia)" : cr.ok ? ' <span class="good">✓</span>' : mixDue() ? ' <span class="warn">deve ancora cambiare</span>' : ""}</span></div>` : ""}
        ${swTxt ? `<div class="row"><span class="k">Commissari</span><span class="v">${swTxt}</span></div>` : ""}
        ${isRace() ? `<div class="row"><span class="k">Se entra ora</span><span class="v" style="text-align:right">${isRedFlag() ? RED_PIT : exitTxt}</span></div>
        <div class="row"><span class="k">Trend gomma (10 giri)</span><span class="v">${degTxt}</span></div>
        <div class="row"><span class="k">Undercut da dietro</span><span class="v" style="text-align:right">${uTxt}</span></div>` : ""}
      </div>`;
    });
    $("#wall-cards").innerHTML = cards.join("") || '<div class="hint">segui un pilota o segna dei preferiti (★) per vederli qui</div>';
  }

  // ------------------------------------------------------------ grafico gap
  let gapChart, gapKey = "";
  function renderGaps() {
    const el = $("#chart-gaps");
    if ($("#gaps").hidden || !isVisible(el)) return;
    if (!isRace()) { el.innerHTML = '<div class="hint">disponibile in gara</div>'; gapChart?.destroy(); gapChart = null; gapKey = ""; return; }
    const me = byNum(prefs.follow);
    const nums = [...new Set([prefs.follow, me && state.drivers.find((d) => d.pos === me.pos - 1)?.num, me && state.drivers.find((d) => d.pos === me.pos + 1)?.num, ...prefs.favs].filter(Boolean))];
    if (!nums.length) { el.innerHTML = '<div class="hint">segui un pilota per vedere il suo gap e quello dei vicini</div>'; gapChart?.destroy(); gapChart = null; gapKey = ""; return; }
    const key = nums.join(",");
    if (key !== gapKey) {
      gapKey = key; gapChart?.destroy(); el.innerHTML = "";
      gapChart = new uPlot({ width: el.clientWidth || 400, height: 200, cursor: { show: false }, scales: { x: { time: false }, y: { dir: -1 } },
        axes: [{ stroke: "#8b93a1", grid: { stroke: "#23272e" }, label: "giro" }, { stroke: "#8b93a1", grid: { stroke: "#23272e" }, size: 50 }],
        series: [{ label: "giro" }, ...nums.map((n) => ({ label: tlaOf(n), stroke: colourOf(n), width: n === prefs.follow ? 3 : 1.5, spanGaps: true }))] }, [[]], el);
    }
    const tables = nums.map((n) => { const h = (byNum(n)?.lap_history || []).filter((l) => l[2] != null); return [h.map((l) => l[0]), h.map((l) => l[2])]; });
    const joined = uPlot.join(tables);
    gapChart.setSize({ width: el.clientWidth || 400, height: 200 });
    gapChart.setData(joined.length ? joined : [[]]);
  }

  // ------------------------------------------------------------ radio (incrementale, mai ricostruita)
  const radioSeen = new Map(); let radioReady = false, radioPath = null;  // path → <li>
  /** Testo del radio con le parole calde evidenziate: toccandole compare la spiegazione. */
  function radioText(r) {
    if (r.status === "spenta") return "";
    if (r.status === "in trascrizione") return '<span class="hint">in trascrizione…</span>';
    if (r.status === "rumore") return '<span class="hint">solo rumore</span>';
    if (r.status === "errore" || !r.text) return '<span class="hint">trascrizione non riuscita</span>';
    let html = esc(r.text);
    for (const h of r.hot || []) {
      html = html.replace(new RegExp(`\\b(${h.w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})\\b`, "gi"), `<mark data-it="${esc(h.it)}">$1</mark>`);
    }
    return `“${html}”`;
  }
  /** Filtro del pannello: tutti, solo seguito e preferiti, oppure solo quelli con parole calde. */
  const radioShown = (r) => prefs.radioFilter === "mine" ? (r.num === prefs.follow || isFav(r.num))
    : prefs.radioFilter === "hot" ? (r.hot || []).length > 0 : true;
  function renderRadio() {
    const ul = $("#radio-list");
    if (state.session.path !== radioPath) {  // sessione nuova: via le radio vecchie, niente autoplay a raffica
      radioPath = state.session.path; radioSeen.clear(); ul.innerHTML = ""; radioReady = false;
    }
    $("#radio-count").textContent = state.radio.length ? `${state.radio.length} messaggi` : "";
    if (!state.radio.length && !ul.children.length) ul.innerHTML = '<li class="hint">ancora nessun team radio</li>';
    for (const r of state.radio) {
      let li = radioSeen.get(r.path);
      if (!li) {
        if (!radioSeen.size) ul.innerHTML = "";
        li = document.createElement("li");
        li.className = radioReady ? "new" : "";
        li.innerHTML = `<span class="t">${localTime(r.utc)}</span><span class="who" style="color:${colourOf(r.num)}">${esc(r.tla)}</span>`
          + `<audio controls preload="metadata" src="/audio?p=${encodeURIComponent(r.path)}"></audio><span class="rtxt"></span><span class="gloss"></span>`;
        ul.prepend(li);
        radioSeen.set(r.path, li);
        if (radioReady && prefs.autoplay && (!prefs.follow || r.num === prefs.follow || isFav(r.num))) li.querySelector("audio").play().catch(() => {});
      }
      // giro e testo arrivano dopo (trascrizione in corso): si aggiornano solo se cambiano
      const tot = state.session.total_laps, after = isRace() && tot && r.lap > tot;  // dopo la bandiera a scacchi
      const t = `${localTime(r.utc)}${r.lap ? ` · ${after ? "arrivo" : "G" + r.lap}` : ""}`;
      if (li.dataset.t !== t) { li.dataset.t = t; li.querySelector(".t").textContent = t; }
      const key = `${r.status}|${r.text}`;
      if (li.dataset.k !== key) { li.dataset.k = key; li.querySelector(".rtxt").innerHTML = radioText(r); }
      li.classList.toggle("hot", (r.hot || []).length > 0);
      li.hidden = !radioShown(r);
    }
    radioReady = true;
  }

  // ------------------------------------------------------------ direzione gara
  let rcCount = -1, chimeReady = false;
  function chime() {
    try { const ac = new AudioContext(), o = ac.createOscillator(), g = ac.createGain(); o.frequency.value = 880; g.gain.value = 0.08; o.connect(g).connect(ac.destination); o.start(); o.stop(ac.currentTime + 0.25); } catch {}
  }
  /** Fascia sempre in vista con l'ultimo messaggio: la direzione gara non deve stare dietro una scheda. */
  function renderRcTicker(list) {
    const el = $("#rc-ticker"), last = list[0];
    el.hidden = !last;
    if (!last) return;
    el.className = `flag-${(last.Flag || "").replace(/\s+/g, "-")} cat-${last.Category || ""}`;
    $("#rc-ticker-lap").textContent = last.Lap ? `G${last.Lap}` : "";
    $("#rc-ticker-msg").textContent = last.Message || "";
    $("#rc-ticker-more").textContent = list.length > 1 ? `+${list.length - 1}` : "";
  }

  const RC_FILTERS = {
    all: () => true,
    flag: (m) => m.Category === "Flag",
    stewards: (m) => /STEWARD|INVESTIGAT|NOTED|PENALTY|REPRIMAND|BLACK AND WHITE|WARNING/.test(m.Message || ""),
    limits: (m) => /TRACK LIMITS/.test(m.Message || ""),
    sc: (m) => m.Category === "SafetyCar" || /SAFETY CAR|\bVSC\b/.test(m.Message || ""),
  };
  let rcKey = "", incKey = "";

  /** Incidenti seguiti dal server dal "notato" all'esito: prima quelli ancora aperti. */
  function renderIncidents() {
    const el = $("#incidents"), show = prefs.rcFilter === "all" || prefs.rcFilter === "stewards";
    const inc = show ? [...(state.incidents || [])].reverse().sort((a, b) => a.closed - b.closed) : [];
    const key = JSON.stringify(inc);
    if (key === incKey) return;
    incKey = key;
    el.innerHTML = inc.map((i) => {
      const cls = !i.closed ? "open" : /penalit|stop|drive|squalif/.test(i.status) ? "pen" : "";
      const where = [i.turn ? `curva ${i.turn}` : "", i.lap ? `G${i.lap}` : ""].filter(Boolean).join(" · ");
      return `<div class="inc ${cls}"><span class="who">${esc(i.cars.map(tlaOf).join(" · "))}</span><span class="hint">${where}</span>`
        + `<span class="st">${esc(i.status)}</span>${i.reason ? `<span class="why">${esc(i.reason.toLowerCase())}</span>` : ""}</div>`;
    }).join("");
  }

  function renderRaceControl() {
    const list = [...state.race_control].reverse();
    renderRcTicker(list);
    renderIncidents();
    // il server manda solo gli ultimi 60 messaggi: la lunghezza della lista smette di crescere
    // (a Monza già al giro 4), quindi si guarda il totale
    const total = state.race_control_total ?? list.length;
    if (total !== rcCount) {
      if (chimeReady && prefs.chime && total > rcCount) chime();
      rcCount = total; chimeReady = true;
    }
    const key = `${prefs.rcFilter}:${total}`;
    if (key === rcKey) return;
    rcKey = key;
    $("#rc-list").innerHTML = list.filter(RC_FILTERS[prefs.rcFilter] || RC_FILTERS.all)
      .map((m) => `<li class="flag-${esc(m.Flag || "")} cat-${esc(m.Category || "")}"><span class="lap">${m.Lap ? "G" + m.Lap : ""}</span><span>${esc(m.Message)}</span></li>`).join("")
      || '<li class="hint">nessun messaggio di questo tipo</li>';
  }

  // ------------------------------------------------------------ prestazioni
  /** Top 5 di una classifica (settore o velocità), più il seguito se è fuori. */
  function rankList(title, pick) {
    const rows = state.drivers.map((d) => ({ d, r: pick(d) })).filter((x) => x.r?.pos && x.r.v).sort((a, b) => a.r.pos - b.r.pos);
    const top = rows.slice(0, 5), me = rows.find((x) => x.d.num === prefs.follow);
    if (me && !top.includes(me)) top.push(me);
    if (!top.length) return `<div class="rk"><h3>${title}</h3><div class="empty">nessun tempo ancora</div></div>`;
    return `<div class="rk"><h3>${title}</h3><ol>${top.map(({ d, r }) =>
      `<li class="${d.num === prefs.follow ? "me" : ""}"><span>${r.pos}. <b style="background:${d.colour}">&nbsp;</b> ${esc(d.tla)}</span><span class="${r.pos === 1 ? "of" : ""}">${esc(r.v)}</span></li>`).join("")}</ol></div>`;
  }

  let lapChart, lapKey = "";
  function renderPerf() {
    if ($("#perf").hidden || !isVisible($("#perf"))) return;
    // giro teorico: primi 10 più seguito e preferiti
    const withTheo = state.drivers.filter((d) => d.bests?.theoretical).sort((a, b) => a.bests.theoretical.seconds - b.bests.theoretical.seconds);
    const shown = withTheo.filter((d, i) => i < 10 || d.num === prefs.follow || isFav(d.num));
    $("#perf-theo").innerHTML = !shown.length ? '<div class="hint">servono tre settori completati</div>'
      : `<table class="theo"><thead><tr><th>#</th><th>Pilota</th><th class="r col-x">Migliore</th><th class="r">Teorico</th><th class="r">Lasciato</th></tr></thead><tbody>${shown.map((d) => {
        const t = d.bests.theoretical, i = withTheo.indexOf(d) + 1;
        return `<tr class="${d.num === prefs.follow ? "follow" : isFav(d.num) ? "fav" : ""}"><td>${i}</td><td><span class="drv"><i style="background:${d.colour}"></i>${esc(d.tla)}</span></td>`
          + `<td class="r col-x">${esc(d.best) || "–"}</td><td class="r ${i === 1 ? "of" : ""}">${esc(t.time)}</td>`
          + `<td class="r ${t.margin > 0.3 ? "warn" : ""}">${t.margin == null ? "–" : t.margin > 0 ? "+" + t.margin.toFixed(3) : "0.000"}</td></tr>`;
      }).join("")}</tbody></table>`;
    const sec = (i) => (d) => d.bests?.sectors?.[i], spd = (k) => (d) => d.bests?.speeds?.[k];
    $("#perf-ranks").innerHTML = rankList("Settore 1", sec(0)) + rankList("Settore 2", sec(1)) + rankList("Settore 3", sec(2)) + rankList("Trappola km/h", spd("st"));

    // posizioni giro per giro: tutti, in evidenza seguito e preferiti
    const el = $("#chart-laps"), drivers = state.drivers.filter((d) => d.lap_positions?.length > 1);
    if (!drivers.length) { el.innerHTML = '<div class="hint">servono almeno due giri</div>'; lapChart?.destroy(); lapChart = null; lapKey = ""; return; }
    const n = Math.max(...drivers.map((d) => d.lap_positions.length));
    const key = [n, prefs.follow, prefs.favs.join(","), drivers.map((d) => d.num).join(","), el.clientWidth].join("|");
    if (key === lapKey) return;
    lapKey = key; lapChart?.destroy(); el.innerHTML = "";
    const hi = (d) => d.num === prefs.follow || isFav(d.num);
    const xs = Array.from({ length: n }, (_, i) => i);
    lapChart = new uPlot({
      width: el.clientWidth || 600, height: 260, cursor: { show: false }, legend: { show: false },
      scales: { x: { time: false }, y: { dir: -1, range: [0.5, state.drivers.length + 0.5] } },
      axes: [{ stroke: "#8b93a1", grid: { stroke: "#23272e" }, label: "giro (0 = griglia)" }, { stroke: "#8b93a1", grid: { stroke: "#23272e" }, size: 34, splits: () => [1, 5, 10, 15, 20].filter((v) => v <= state.drivers.length) }],
      series: [{}, ...drivers.map((d) => ({ stroke: hi(d) ? d.colour : d.colour + "40", width: d.num === prefs.follow ? 3 : hi(d) ? 2 : 1, spanGaps: true }))],
    }, [xs, ...drivers.map((d) => xs.map((i) => d.lap_positions[i] ?? null))], el);
  }

  // ------------------------------------------------------------ confronto giri (archivio F1)
  let cmpPath = null, cmpData = null, cmpKey = "";
  const cmpCharts = {};
  const cmpStatus = (t) => { $("#cmp-status").textContent = t; };

  /** Sessioni del weekend già in archivio: si ricaricano quando cambia la sessione seguita. */
  function renderCmp() {
    if ($("#cmp").hidden || !isVisible($("#cmp"))) return;
    if (cmpPath !== state.session.path) {
      cmpPath = state.session.path;
      fetch("/api/sessions").then((r) => r.json()).then((list) => {
        const sel = $("#cmp-session");
        if (!list.length) { sel.innerHTML = ""; cmpStatus("nessuna sessione di questo weekend è ancora in archivio"); return; }
        setOptions(sel, list.map((s) => [s.path, s.name]), list.at(-1).path);
        loadCmpLaps();
      }).catch(() => cmpStatus("non riesco a leggere l'elenco delle sessioni"));
    }
    drawCmp();
  }

  function loadCmpLaps() {
    const path = $("#cmp-session").value; if (!path) return;
    cmpStatus("scarico la telemetria della sessione dall'archivio F1 (la prima volta ci vuole qualche secondo)…");
    $("#cmp-go").disabled = true;
    fetch(`/api/laps?path=${encodeURIComponent(path)}`).then((r) => r.ok ? r.json() : Promise.reject(r)).then(({ drivers }) => {
      const items = drivers.map((d) => [d.num, `${d.tla} · ${d.time}`]);
      // di default: il pilota seguito e i più veloci della sessione
      const picks = [prefs.follow, ...drivers.map((d) => d.num)].filter((n, i, a) => n && drivers.some((d) => d.num === n) && a.indexOf(n) === i).slice(0, 3);
      document.querySelectorAll(".cmp-driver").forEach((sel, i) => setOptions(sel, [["", "—"], ...items], picks[i] || ""));
      $("#cmp-go").disabled = false;
      cmpStatus(drivers.length ? "" : "in questa sessione non ci sono giri con telemetria");
      if (drivers.length) runCompare();
    }).catch(() => cmpStatus("archivio F1 non raggiungibile: riprova tra poco"));
  }

  function runCompare() {
    const path = $("#cmp-session").value;
    const nums = [...document.querySelectorAll(".cmp-driver")].map((s) => s.value).filter(Boolean);
    if (!path || !nums.length) return;
    cmpStatus("confronto i giri…");
    fetch(`/api/compare?path=${encodeURIComponent(path)}&drivers=${nums.join(",")}`).then((r) => r.json()).then((data) => {
      cmpData = data; cmpKey = "";
      cmpStatus(data.skipped?.length ? `${data.skipped.join(", ")}: nessun giro con telemetria buona in questa sessione` : "");
      drawCmp();
    }).catch(() => cmpStatus("confronto non riuscito"));
  }

  function drawCmp() {
    const box = $("#cmp-charts");
    box.hidden = !cmpData?.drivers?.length;
    if (box.hidden) { $("#cmp-summary").innerHTML = ""; return; }
    const width = $("#cmp-speed").clientWidth || 800, key = `${width}|${JSON.stringify(cmpData.drivers.map((d) => [d.num, d.time]))}`;
    if (key === cmpKey) return;
    cmpKey = key;
    const ds = cmpData.drivers;
    // compagni di squadra hanno lo stesso colore: il secondo si disegna tratteggiato
    const dashed = ds.map((d, i) => ds.slice(0, i).some((e) => e.colour === d.colour));
    $("#cmp-summary").innerHTML = ds.map((d, i) => {
      const gap = i === 0 ? "riferimento" : `+${d.delta.at(-1).toFixed(3)} s`;
      return `<div class="row" style="border-left-color:${d.colour};border-left-style:${dashed[i] ? "dashed" : "solid"}"><b>${esc(d.tla)}</b>${dashed[i] ? '<span class="hint">(tratteggiato)</span>' : ""}<span>giro ${esc(d.lap)}</span><span>${esc(d.time)}</span>`
        + `<span class="${i ? "warn" : "good"}">${gap}</span><span class="hint">punta ${d.top_speed ?? "–"} km/h</span>${d.note ? `<span class="note">${esc(d.note)}</span>` : ""}</div>`;
    }).join("");
    const x = cmpData.distance, corners = cmpData.corners || [];
    // curve come riferimento sull'asse: si scrivono solo quelle abbastanza distanti da non sovrapporsi
    const minGap = (x.at(-1) || 6000) / Math.max(4, width / 34), shown = new Set();
    let lastLabel = -Infinity;
    for (const c of [...corners].sort((a, b) => a.d - b.d)) if (c.d - lastLabel >= minGap) { shown.add(c.d); lastLabel = c.d; }
    const xAxis = { stroke: "#8b93a1", grid: { stroke: "#23272e" },
      splits: corners.length ? () => corners.map((c) => c.d) : undefined,
      values: corners.length ? (u, vs) => vs.map((v) => { const c = corners.find((k) => k.d === v); return c && shown.has(v) ? `C${c.n}` : ""; }) : undefined };
    const make = (id, field, opts = {}) => {
      cmpCharts[id]?.destroy(); const el = $(`#cmp-${id}`); el.innerHTML = "";
      cmpCharts[id] = new uPlot({
        width, height: opts.height || 150, cursor: { sync: { key: "cmp" }, drag: { x: false, y: false } }, legend: { show: true },
        scales: { x: { time: false }, y: opts.range ? { range: opts.range } : {} },
        axes: [xAxis, { stroke: "#8b93a1", grid: { stroke: "#23272e" }, size: 46 }],
        series: [{ label: "m", value: (u, v) => v == null ? "–" : `${Math.round(v)} m` },
          ...ds.map((d, i) => ({ label: d.tla, stroke: d.colour, width: i === 0 ? 2 : 1.6, dash: dashed[i] ? [6, 4] : undefined,
            paths: opts.stepped ? uPlot.paths.stepped({ align: 1 }) : undefined }))],
      }, [x, ...ds.map((d) => d[field])], el);
    };
    make("delta", "delta", { height: 170 });
    make("speed", "speed", { height: 190 });
    make("throttle", "throttle", { range: [0, 100] });
    make("brake", "brake", { height: 90, range: [0, 100], stepped: true });
    make("gear", "gear", { height: 110, range: [0, 8], stepped: true });
  }

  // ------------------------------------------------------------ campionato
  let champKey = "";
  function renderChamp() {
    const c = state.championship || {}, key = JSON.stringify(c) + prefs.follow;
    if (key === champKey) return;
    champKey = key;
    if (!c.drivers?.length) {
      $("#champ-hint").textContent = "";
      $("#champ-body").innerHTML = '<div class="hint">disponibile in gara: la F1 lo manda solo durante il Gran Premio</div>';
      return;
    }
    $("#champ-hint").textContent = "punti e posizione se la gara finisse ora";
    const arrow = (r) => r.pos && r.pred_pos && r.pred_pos !== r.pos
      ? `<span class="${r.pred_pos < r.pos ? "up" : "down"}">${r.pred_pos < r.pos ? "▲" : "▼"}${Math.abs(r.pos - r.pred_pos)}</span>` : "";
    const table = (title, rows, label) => `<div><table><thead><tr><th>#</th><th>${title}</th><th class="r">Ora</th><th class="r">Previsti</th></tr></thead><tbody>${rows.map((r) =>
      `<tr class="${r.key === prefs.follow ? "follow" : ""}"><td>${r.pred_pos ?? "–"} ${arrow(r)}</td><td>${label(r)}</td><td class="r">${r.points ?? "–"}</td>`
      + `<td class="r"><b>${r.pred_points ?? "–"}</b>${r.points != null && r.pred_points != null && r.pred_points !== r.points ? ` <span class="up">+${r.pred_points - r.points}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
    $("#champ-body").innerHTML = `<div class="champ">${table("Pilota", c.drivers, (r) => byNum(r.key)
      ? `<span class="drv"><i style="background:${colourOf(r.key)}"></i>${esc(tlaOf(r.key))}</span>`
      : `<span class="hint" title="in classifica ma non in questa sessione">n° ${esc(r.key)}</span>`)}${table("Squadra", c.teams, (r) => esc(r.key))}</div>`;
  }

  // ------------------------------------------------------------ telemetria (uPlot)
  const charts = {};
  const selected = () => [...document.querySelectorAll(".tel-driver")].map((s) => s.value).filter(Boolean);
  function mkChart(el, opts) {
    const o = Object.assign({ width: el.clientWidth || 800, height: 150, cursor: { show: false }, legend: { show: true },
      scales: { x: { time: false } }, axes: [{ stroke: "#8b93a1", grid: { stroke: "#23272e" } }, { stroke: "#8b93a1", grid: { stroke: "#23272e" }, size: 46 }], series: [{}] }, opts);
    return new uPlot(o, [[]], el);
  }
  function buildSeries(kind, nums) {
    const series = [{ label: "s" }];
    for (const n of nums) {
      const c = colourOf(n), tla = tlaOf(n);
      if (kind === "speed" || kind === "rpm") series.push({ label: tla, stroke: c, width: 2, spanGaps: true });
      else if (kind === "pedals") { series.push({ label: `${tla} gas`, stroke: c, width: 1.5, spanGaps: true }); series.push({ label: `${tla} freno`, stroke: c, width: 1.5, dash: [4, 3], spanGaps: true }); }
      else series.push({ label: tla, stroke: c, width: 2, spanGaps: true, paths: uPlot.paths.stepped({ align: 1 }) });
    }
    return series;
  }
  let chartKey = "";
  function renderTelemetry() {
    if ($("#telemetry").hidden || !isVisible($("#telemetry"))) return;
    // in diretta la F1 manda la telemetria solo agli abbonati F1 TV: meglio dirlo che mostrare grafici vuoti
    const none = !Object.keys(tel).length;
    $("#tel-hint").hidden = !none;
    $("#telemetry .charts").hidden = none;
    if (none) return;
    const nums = selected(), key = nums.join(",");
    const els = { speed: $("#chart-speed"), pedals: $("#chart-pedals"), rpm: $("#chart-rpm"), gear: $("#chart-gear") };
    if (key !== chartKey) {
      chartKey = key;
      for (const k of Object.keys(els)) { charts[k]?.destroy(); els[k].innerHTML = ""; }
      charts.speed = mkChart(els.speed, { series: buildSeries("speed", nums), scales: { x: { time: false }, y: { range: [0, 360] } } });
      charts.pedals = mkChart(els.pedals, { series: buildSeries("pedals", nums), scales: { x: { time: false }, y: { range: [0, 100] } } });
      charts.rpm = mkChart(els.rpm, { height: 120, series: buildSeries("rpm", nums), scales: { x: { time: false }, y: { range: [0, 13000] } },
        axes: [{ stroke: "#8b93a1", grid: { stroke: "#23272e" } }, { stroke: "#8b93a1", grid: { stroke: "#23272e" }, size: 46, values: (u, vs) => vs.map((v) => v >= 1000 ? `${v / 1000}k` : v) }] });
      charts.gear = mkChart(els.gear, { height: 110, series: buildSeries("gear", nums), scales: { x: { time: false }, y: { range: [0, 8] } } });
    }
    if (!nums.length) return;
    const now = Math.max(0, ...nums.map((n) => tel[n]?.at(-1)?.t ?? 0));
    const tables = { speed: [], pedals: [], rpm: [], gear: [] };
    for (const n of nums) {
      const rows = (tel[n] || []).filter((s) => s.t >= now - WINDOW);
      const xs = rows.map((s) => +(s.t - now).toFixed(2));
      tables.speed.push([xs, rows.map((s) => s.speed)]);
      tables.pedals.push([xs, rows.map((s) => s.throttle), rows.map((s) => s.brake ? 100 : 0)]);
      tables.rpm.push([xs, rows.map((s) => s.rpm)]);
      tables.gear.push([xs, rows.map((s) => s.gear)]);
    }
    const H = { gear: 110, rpm: 120 };
    for (const k of Object.keys(tables)) {
      const joined = uPlot.join(tables[k]);
      charts[k].setSize({ width: els[k].clientWidth || 800, height: H[k] || 150 });
      charts[k].setData(joined.length ? joined : [[]]);
      charts[k].setScale("x", { min: -WINDOW, max: 0 });
    }
  }

  // ------------------------------------------------------------ posizioni stimate (senza GPS)
  // In diretta la F1 non manda le posizioni GPS a chi non è abbonato: arrivano solo i minisettori.
  // Da quelli si ricava la frazione di giro e si mette la macchina in quel punto del tracciato.

  /** Tracciato in ordine di marcia a partire dal traguardo, con la distanza progressiva.
   *  Traguardo = inizio del settore commissari 1; verso = quello che porta alla curva 1. */
  let trackModel = null;
  function getTrackModel() {
    if (trackModel?.src === mapData) return trackModel;
    const pts = mapData.x.map((x, i) => [x, mapData.y[i]]), n = pts.length;
    const nearest = (x, y) => { let best = 0, bd = Infinity; pts.forEach(([px, py], i) => { const d = (px - x) ** 2 + (py - y) ** 2; if (d < bd) { bd = d; best = i; } }); return best; };
    const ms1 = mapData.marshal_sectors?.find((m) => Number(m.n) === 1);
    const start = ms1 ? nearest(ms1.x, ms1.y) : 0;
    const c1 = mapData.corners?.find((c) => Number(c.n) === 1);
    const dir = c1 && (nearest(c1.x, c1.y) - start + n) % n > n / 2 ? -1 : 1;
    const order = Array.from({ length: n }, (_, k) => pts[((start + dir * k) % n + n) % n]);
    const cum = [0];
    for (let k = 1; k <= n; k++) { const [ax, ay] = order[k - 1], [bx, by] = order[k % n]; cum.push(cum[k - 1] + Math.hypot(bx - ax, by - ay)); }
    return (trackModel = { src: mapData, order, cum, total: cum[n] });
  }
  /** Punto del tracciato (coordinate originali) a una certa frazione di giro. */
  function pointAt(frac) {
    const { order, cum, total } = getTrackModel(), d = (((frac % 1) + 1) % 1) * total;
    let lo = 0, hi = cum.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= d) lo = mid; else hi = mid; }
    const t = (d - cum[lo]) / ((cum[hi] - cum[lo]) || 1), a = order[lo % order.length], b = order[hi % order.length];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }
  /** Stato nel giro: minisettori accesi di fila dall'inizio, su quanti sono in tutto.
   *  Contare solo quelli "di fila" scarta i minisettori rimasti accesi dal giro prima.
   *  Stessa regola di lap_state() in muretto/calibration.py: devono restare uguali. */
  function lapState(d) {
    const segs = d.sectors.flatMap((s) => s.seg);
    if (!segs.length) return null;
    let k = 0;
    while (k < segs.length && segs[k]) k++;
    return { k, n: segs.length };
  }
  /** Quando ogni macchina è entrata nello stato attuale (orologio della pagina). */
  const stateSince = {};
  /** Punto a una frazione `f` (0–1) di una spezzata. */
  function along(path, f) {
    const x = f * (path.length - 1), i = Math.floor(x), t = x - i, a = path[i], b = path[Math.min(i + 1, path.length - 1)];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }
  /** Dove disegnare una macchina senza GPS. Con la calibrazione del server (sessione già
   *  finita sullo stesso circuito) avanza lungo il tratto dello stato col tempo passato:
   *  a Baku errore mediano 25 m. Senza, a metà del minisettore: ~180 m. */
  function estimatedPoint(d) {
    const s = lapState(d); if (!s) return null;
    const now = performance.now() / 1000, seen = stateSince[d.num];
    // appena aperta la pagina non si sa da quanto c'è: meglio il punto tipico che l'inizio del tratto
    if (!seen || seen.k !== s.k) stateSince[d.num] = { k: s.k, t: now, known: !!seen };
    const since = stateSince[d.num], cal = mapData.seg_points, st = cal && cal.n === s.n ? cal.states[s.k] : null;
    if (st?.path && st.dur && since.known) return along(st.path, Math.min(0.95, (now - since.t) / st.dur));
    if (st?.point) return st.point;
    return pointAt((s.k + 0.5) / s.n);
  }

  // ------------------------------------------------------------ mappa
  let mapRev = -1;
  function loadMap() {
    const rev = state.session.map_rev || 0;
    if (state.session.path === mapPath && rev === mapRev) return;
    // cambio sessione: via la mappa vecchia. Solo la calibrazione nuova: si tiene finché arriva
    if (state.session.path !== mapPath) mapData = null;
    mapPath = state.session.path; mapRev = rev;
    fetch("/api/map").then((r) => r.json()).then((m) => { if (m.x?.length) mapData = m; }).catch(() => {});
  }
  function yellowSectors() {
    const on = new Set();
    for (const m of state.race_control) {
      const y = /(?:DOUBLE )?YELLOW IN TRACK SECTOR (\d+)/.exec(m.Message || ""), c = /CLEAR IN TRACK SECTOR (\d+)/.exec(m.Message || "");
      if (y) on.add(+y[1]); if (c) on.delete(+c[1]);
      if (/GREEN LIGHT|TRACK CLEAR|RACE RESTART|SAFETY CAR IN THIS LAP/.test(m.Message || "")) on.clear();
    }
    if (state.session.track === "green") on.clear();
    return on;
  }
  function renderMap() {
    if ($("#map").hidden || !isVisible($("#map"))) return;
    loadMap();
    const cv = $("#map-canvas"), ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    const rot = mapData ? (mapData.rotation || 0) * Math.PI / 180 : 0;
    const base = mapData ? mapData.x.map((x, i) => [x, mapData.y[i]]) : Object.values(trail).flat();
    if (base.length < 20) { ctx.fillStyle = "#8b93a1"; ctx.font = "12px sans-serif"; ctx.fillText("in attesa delle posizioni GPS o del tracciato", 14, 24); return; }
    let cx = 0, cy = 0; for (const [x, y] of base) { cx += x; cy += y; } cx /= base.length; cy /= base.length;
    const R = ([x, y]) => [cx + (x - cx) * Math.cos(rot) - (y - cy) * Math.sin(rot), cy + (x - cx) * Math.sin(rot) + (y - cy) * Math.cos(rot)];
    const pts = base.map(R);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of pts) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
    const pad = 36, sc = Math.min((cv.width - 2 * pad) / (maxX - minX || 1), (cv.height - 2 * pad) / (maxY - minY || 1));
    const ox = (cv.width - (maxX - minX) * sc) / 2, oy = (cv.height - (maxY - minY) * sc) / 2;
    const P = ([x, y]) => [ox + (x - minX) * sc, cv.height - (oy + (y - minY) * sc)];
    if (mapData) {
      ctx.lineWidth = 8; ctx.lineJoin = "round"; ctx.strokeStyle = "#2c323b"; ctx.beginPath();
      pts.forEach((p, i) => { const [x, y] = P(p); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath(); ctx.stroke();
      // settori dei commissari in giallo
      const yel = yellowSectors();
      if (yel.size && mapData.marshal_sectors?.length) {
        const idx = mapData.marshal_sectors.map((m) => { const [mx, my] = R([m.x, m.y]); let best = 0, bd = Infinity; pts.forEach(([x, y], i) => { const d = (x - mx) ** 2 + (y - my) ** 2; if (d < bd) { bd = d; best = i; } }); return [m.n, best]; });
        ctx.strokeStyle = "#f5c518"; ctx.lineWidth = 8;
        idx.forEach(([n, start], k) => {
          if (!yel.has(n)) return;
          const end = idx[(k + 1) % idx.length][1];
          ctx.beginPath();
          let i = start, first = true;
          do { const [x, y] = P(pts[i]); first ? ctx.moveTo(x, y) : ctx.lineTo(x, y); first = false; i = (i + 1) % pts.length; } while (i !== end);
          ctx.stroke();
        });
      }
      ctx.fillStyle = "#8b93a1"; ctx.font = "10px monospace";
      for (const c of mapData.corners) { const [x, y] = P(R([c.x, c.y])); const a = (c.angle + (mapData.rotation || 0)) * Math.PI / 180; ctx.fillText(c.n, x + 14 * Math.cos(a) - 3, y - 14 * Math.sin(a) + 3); }
      $("#map-hint").textContent = yel.size ? `bandiera gialla nei settori ${[...yel].join(", ")}` : "";
    } else {
      ctx.fillStyle = "#2c323b"; for (const p of pts) { const [x, y] = P(p); ctx.fillRect(x - 1, y - 1, 2, 2); }
      $("#map-hint").textContent = "tracciato ricostruito dai GPS";
    }
    // GPS vero se arriva (replay), altrimenti posizione stimata dai minisettori (diretta)
    const gps = Object.keys(lastPos).length > 0, estimate = !gps && !!mapData;
    if (estimate) {  // si aggiunge all'eventuale avviso di bandiera gialla, riscritto a ogni disegno
      const h = $("#map-hint"), src = mapData.seg_points?.source;
      h.textContent = (h.textContent ? h.textContent + " · " : "")
        + (src ? `posizioni stimate dai minisettori, calibrate su ${src}` : "posizioni stimate a grandi linee dai minisettori")
        + ": in diretta la F1 non manda il GPS";
    }
    // sigle: prima seguito e preferiti, poi le altre solo dove non si sovrappongono
    const labels = [];
    const order = [...state.drivers].sort((a, b) => (b.num === prefs.follow) - (a.num === prefs.follow) || isFav(b.num) - isFav(a.num));
    for (const d of order) {
      let raw;
      if (gps) {
        const p = lastPos[d.num]; if (!p || d.retired || p.status !== "OnTrack") continue;
        raw = [p.x, p.y];
      } else if (estimate) {
        if (d.retired || d.stopped || d.inpit) continue;
        raw = estimatedPoint(d); if (!raw) continue;
      } else continue;
      const [x, y] = P(R(raw)), hi = d.num === prefs.follow, fav = isFav(d.num);
      ctx.beginPath(); ctx.arc(x, y, hi ? 8 : fav ? 6.5 : 5, 0, Math.PI * 2); ctx.fillStyle = d.colour;
      ctx.globalAlpha = estimate ? 0.85 : 1; ctx.fill(); ctx.globalAlpha = 1;
      if (hi || fav) { ctx.strokeStyle = hi ? "#fff" : "#3b82f6"; ctx.lineWidth = 2; ctx.stroke(); }
      if (hi || fav || labels.every(([lx, ly]) => Math.abs(lx - x) > 30 || Math.abs(ly - y) > 13)) {
        ctx.fillStyle = "#e8eaed"; ctx.font = `${hi ? "bold 12px" : "11px"} monospace`; ctx.fillText(d.tla, x + 9, y + 4);
        labels.push([x, y]);
      }
    }
  }

  // ------------------------------------------------------------ controlli
  $("#follow").addEventListener("change", (e) => { prefs.follow = e.target.value; save("follow", prefs.follow); prefs.telDrivers = []; save("telDrivers", []); document.querySelectorAll(".tel-driver").forEach((s) => { s.innerHTML = ""; }); chartKey = ""; if (state) render(); });
  $("#leaderboard").addEventListener("click", (e) => {
    const td = e.target.closest("td.star");
    if (td) {
      const n = td.parentElement.dataset.num;
      prefs.favs = isFav(n) ? prefs.favs.filter((x) => x !== n) : [...prefs.favs, n]; save("favs", prefs.favs); if (state) render();
      return;
    }
    // su telefono la riga si apre e mostra intervallo, miglior giro, settori, soste e stint
    if (!isMobile()) return;
    const tr = e.target.closest("tr[data-num]"); if (!tr) return;
    openNum = openNum === tr.dataset.num ? "" : tr.dataset.num;
    if (state) renderBoard();
  });
  document.querySelectorAll(".tel-driver").forEach((sel) => sel.addEventListener("change", () => { prefs.telDrivers = [...document.querySelectorAll(".tel-driver")].map((s) => s.value); save("telDrivers", prefs.telDrivers); if (state) renderTelemetry(); }));
  $("#pit-loss").addEventListener("change", (e) => { const v = parseFloat(e.target.value); prefs.pitLoss = isNaN(v) ? null : v; save("pitLoss", prefs.pitLoss); if (state) render(); });
  $("#delay").addEventListener("change", (e) => { prefs.delay = Math.max(0, parseInt(e.target.value) || 0); save("delay", prefs.delay); send({ delay: prefs.delay }); });
  document.querySelectorAll("[data-panel]").forEach((cb) => cb.addEventListener("change", () => { prefs.panels[cb.dataset.panel] = cb.checked; save("panels", prefs.panels); chartKey = ""; if (state) render(); }));
  $("#opt-metrics").addEventListener("change", (e) => { prefs.metrics = e.target.checked; save("metrics", prefs.metrics); if (state) render(); });
  $("#opt-autoplay").addEventListener("change", (e) => { prefs.autoplay = e.target.checked; save("autoplay", prefs.autoplay); });
  $("#opt-chime").addEventListener("change", (e) => { prefs.chime = e.target.checked; save("chime", prefs.chime); });
  $("#opt-wall-all").addEventListener("change", (e) => { prefs.wallAll = e.target.checked; save("wallAll", prefs.wallAll); if (state) renderWall(); });
  window.addEventListener("resize", () => { chartKey = ""; gapKey = ""; if (state) { renderTelemetry(); renderGaps(); } });

  // ------------------------------------------------------------ schede e impostazioni (telefono)
  /** Mostra un solo pannello. I grafici vanno ricostruiti: fino a un attimo prima avevano larghezza 0. */
  function setTab(name) {
    prefs.tab = name; save("tab", name);
    document.body.dataset.tab = name;
    for (const p of document.querySelectorAll("main .panel")) p.classList.toggle("tab-active", p.id === name);
    for (const b of $("#tabbar").children) b.classList.toggle("on", b.dataset.tab === name);
    chartKey = ""; gapKey = ""; lapKey = ""; cmpKey = "";
    if (state) { renderTelemetry(); renderGaps(); renderMap(); renderPerf(); renderCmp(); }
  }
  $("#tabbar").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); });
  $("#rc-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.rcFilter = b.dataset.f; save("rcFilter", prefs.rcFilter);
    for (const x of $("#rc-filters").children) x.classList.toggle("on", x === b);
    rcKey = ""; incKey = ""; if (state) renderRaceControl();
  });
  for (const x of $("#rc-filters").children) x.classList.toggle("on", x.dataset.f === prefs.rcFilter);
  $("#cmp-session").addEventListener("change", loadCmpLaps);
  $("#cmp-go").addEventListener("click", runCompare);
  $("#radio-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.radioFilter = b.dataset.f; save("radioFilter", prefs.radioFilter);
    for (const x of $("#radio-filters").children) x.classList.toggle("on", x === b);
    if (state) renderRadio();
  });
  for (const x of $("#radio-filters").children) x.classList.toggle("on", x.dataset.f === prefs.radioFilter);
  // parola calda toccata: la spiegazione compare sotto il radio
  $("#radio-list").addEventListener("click", (e) => {
    const m = e.target.closest("mark"); if (!m) return;
    const g = m.closest("li").querySelector(".gloss");
    g.textContent = g.textContent.startsWith(m.textContent) ? "" : `${m.textContent} = ${m.dataset.it}`;
  });
  // toccando la fascia si aprono tutti i messaggi
  $("#rc-ticker").addEventListener("click", () => {
    if (isMobile()) setTab("rc");
    else $("#rc").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#settings-toggle").addEventListener("click", (e) => {
    const open = document.body.classList.toggle("settings-open");
    e.currentTarget.setAttribute("aria-expanded", String(open));
  });
  /** Passando fra telefono e desktop cambia sia la scheda del pilota sia chi è visibile. */
  mq.addEventListener("change", () => { if (applyMobileMode()) { chartKey = ""; gapKey = ""; if (state) render(); } });
  $("#opt-mobile").addEventListener("change", (e) => {
    prefs.mobile = e.target.value; save("mobile", prefs.mobile);
    applyMobileMode(); chartKey = ""; gapKey = ""; if (state) render();
  });
  applyMobileMode();
  setTab(prefs.tab);

  connect();
})();
