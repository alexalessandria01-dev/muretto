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
    wallAll: store("wallAll", false), mobile: store("mobile", "auto"), rcFilter: store("rcFilter", "all"),
  };
  /** Pit loss in uso: quella scelta dall'utente, altrimenti quella del server (già adattata a SC/VSC). */
  const pitLossNow = () => prefs.pitLoss ?? state.pit_loss.value;
  const pitLossNormal = () => prefs.pitLoss ?? state.pit_loss.normal;
  const isFav = (n) => prefs.favs.includes(n);

  // ------------------------------------------------------------ websocket
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?delay=${prefs.delay || 0}`);
    ws.onopen = () => $("#conn").classList.add("on");
    ws.onclose = () => { $("#conn").classList.remove("on"); setTimeout(connect, 2000); };
    ws.onmessage = (ev) => {
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

  // ------------------------------------------------------------ helpers
  const fmtClock = (s) => { if (s == null) return "–"; s = Math.floor(s); return [s / 3600, s / 60 % 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, "0")).join(":"); };
  const localTime = (utc) => { const d = new Date(utc); return isNaN(d) ? "" : d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const byNum = (n) => state?.drivers.find((d) => d.num === n);
  const tlaOf = (n) => byNum(n)?.tla || n;
  const colourOf = (n) => byNum(n)?.colour || "#888";
  const TRACK_LABEL = { green: "VERDE", yellow: "GIALLA", sc: "SAFETY CAR", vsc: "VSC", vsc_ending: "VSC FINISCE", red: "ROSSA" };
  const isRace = () => state?.session.type === "Race";
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

  /** Variazione dell'intervallo tra due piloti negli ultimi `n` giri (negativo = si avvicina chi sta dietro). */
  function intervalTrend(front, back, n = 3) {
    const gf = Object.fromEntries((front.lap_history || []).map((l) => [l[0], l[2]]));
    const pts = (back.lap_history || []).filter((l) => l[2] != null && gf[l[0]] != null).map((l) => [l[0], l[2] - gf[l[0]]]);
    if (pts.length < 2) return null;
    const last = pts.slice(-(n + 1));
    return (last.at(-1)[1] - last[0][1]) / (last.length - 1);
  }

  // ------------------------------------------------------------ render
  function render() {
    const s = state.session, w = state.weather;
    $("#session-title").textContent = [s.meeting, s.name, s.circuit].filter(Boolean).join(" · ");
    const ts = $("#track-status"); ts.className = `pill ${s.track}`; ts.textContent = TRACK_LABEL[s.track] || s.track_msg;
    $("#lap").textContent = isRace() ? (s.lap ? `${s.lap}${s.total_laps ? " / " + s.total_laps : ""}` : "–") : (s.part ? `${s.name} · Q${s.part}` : s.name || "–");
    $("#remaining").textContent = s.remaining || "–";
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
      : cell("Ultimo giro", esc(me.last) || "–") + cell("Miglior giro", esc(me.best) || "–")
        + cell("Distacco", gapTxt) + cell("Velocità trap", `${esc(me.speed_trap) || "–"}`);

    const cr = me.compounds;
    const notes = stewardBadges(me) + (isRace() && cr && !cr.ok && !cr.wet && !me.retired ? '<span class="flagb mix">UNA SOLA MESCOLA</span>' : "")
      + ((me.pit_stops || []).length ? `<span class="hint">soste: ${me.pit_stops.map((p) => `G${esc(p.lap)} ${esc(p.stop)} s`).join(" · ")}</span>` : "");
    let pit = "";
    if (isRace() && !me.retired) {
      const e = pitExit(me.num, pitLossNow());
      pit = `<div class="h-pit">Se entra ora (${pitLossNow()} s): ${!e ? "—" : `esce <b>P${e.pos}</b>${e.lost > 0 ? ` <span class="warn">(−${e.lost})</span>` : ' <span class="good">(nessuna posizione persa)</span>'}`}</div>`;
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
      ${pit}`;
  }

  function carCell(d) {
    const c = d.car; if (!c) return "";
    return `<span class="car"><span class="gear">${c.gear ?? ""}</span><span class="spd">${c.speed ?? ""}</span><span class="bars"><i class="th"><b style="width:${c.throttle || 0}%"></b></i><i class="br"><b style="width:${c.brake ? 100 : 0}%"></b></i></span></span>`;
  }

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
      const danger = quali && part && ((part === 1 && d.pos > 15) || (part === 2 && d.pos > 10));
      const cls = [d.num === prefs.follow ? "follow" : isFav(d.num) ? "fav" : "", d.retired || d.stopped ? "retired" : "", d.knocked_out ? "out" : "", danger ? "danger" : "", d.num === openNum ? "open" : ""].join(" ");
      return `<tr class="${cls}" data-num="${d.num}">
        <td class="pos">${d.pos}</td>
        <td class="col-drv"><span class="drv"><i style="background:${d.colour}"></i>${esc(d.tla)}</span>${gained}${drs}${st}${stewardBadges(d)}</td>
        <td class="star ${isFav(d.num) ? "on" : ""}" title="preferito">★</td>
        <td class="r col-gap">${esc(d.gap) || (d.pos === 1 ? '<span class="leader">LEADER</span>' : "")}</td>
        <td class="r col-int ${d.catching ? "catching" : ""}">${esc(d.interval)}</td>
        <td class="r col-last ${d.last_of ? "of" : d.last_pf ? "pf" : ""}">${esc(d.last)}</td>
        <td class="r col-best">${esc(d.best)}</td>
        <td class="col-sectors"><span class="sectors">${sectors}</span></td>
        <td class="col-tyre"><span class="tyre"><b class="${d.compound}"></b>${d.age}${d.new ? "" : '<span class="used">usata</span>'}</span></td>
        <td class="r col-stops">${d.stops}</td>
        <td class="col-stints"><span class="stints">${stints}</span></td>
        <td class="metrics">${carCell(d)}</td>
      </tr>`;
    });
    $("#leaderboard tbody").innerHTML = rows.join("");
  }

  function whoRow(d, me, role) {
    if (!d) return `<div class="who"><span class="p">–</span><span>${role === "ahead" ? "nessuno davanti: è in testa" : "nessuno dietro"}</span></div>`;
    let trend = "";
    if (role !== "me" && isRace()) {
      const t = role === "ahead" ? intervalTrend(d, me) : intervalTrend(me, d);
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
      exit = `<div class="exit">Se entra ora (${pitLossNow()} s): ${fmt(pitExit(me.num, pitLossNow()))}</div>` + (state.session.track === "green" ? `<div class="exit">Sotto Safety Car (${pl.sc} s): ${fmt(sc)}</div>` : `<div class="exit">In condizioni normali (${pitLossNormal()} s): ${fmt(n)}</div>`);
    }
    $("#battle-body").innerHTML = `<div class="battle">${whoRow(ahead, me, "ahead")}${whoRow(me, me, "me")}${whoRow(behind, me, "behind")}</div>${exit}`;
  }

  function renderWall() {
    const mine = [prefs.follow, ...prefs.favs.filter((n) => n !== prefs.follow)].filter(Boolean);
    const nums = prefs.wallAll ? state.drivers.map((d) => d.num) : mine;
    $("#wall-hint").textContent = prefs.wallAll ? `tutti e ${state.drivers.length} i piloti` : "seguito + preferiti";
    const cards = nums.map(byNum).filter(Boolean).map((d) => {
      const loss = pitLossNow(), e = d.retired ? null : pitExit(d.num, loss), deg = d.deg;
      const exitTxt = !e ? "—" : `<span class="big ${e.lost > 0 ? "warn" : "good"}">P${e.pos}</span> ${e.lost > 0 ? `(−${e.lost})` : "(nessuna posizione persa)"}`
        + (e.behind ? `<br>dietro <b>${esc(tlaOf(e.behind[0]))}</b> di ${e.behind[1]} s` : "<br>in testa")
        + (e.aheadOf ? ` · davanti a <b>${esc(tlaOf(e.aheadOf[0]))}</b> di ${e.aheadOf[1]} s` : "");
      const back = state.drivers.find((x) => x.pos === d.pos + 1), ivb = back ? parseFloat(String(back.interval).replace("+", "")) : NaN;
      const u = back && !isNaN(ivb) ? { by: back.tla, interval: ivb, window: ivb < loss + 3, needs_per_lap: Math.max(0, (loss - ivb) / 2).toFixed(2), tyre_delta: d.age - back.age } : null;
      const degTxt = !deg ? "servono 3 giri puliti" : `<span class="${deg.slope > 0.2 ? "bad" : deg.slope > 0.08 ? "warn" : "good"}">${deg.slope > 0 ? "+" : ""}${deg.slope.toFixed(3)} s/giro</span> su ${deg.laps} giri`;
      const uTxt = !u ? "nessuno dietro" : `<b>${esc(u.by)}</b> a ${u.interval} s: ${u.window ? '<span class="warn">in finestra</span>' : '<span class="good">fuori finestra</span>'}`
        + `<br>gli servono ${u.needs_per_lap} s/giro · gomme ${u.tyre_delta > 0 ? `mie +${u.tyre_delta} giri` : u.tyre_delta < 0 ? `sue +${-u.tyre_delta} giri` : "pari"}`;
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
        <div class="row"><span class="k">Giri / soste</span><span class="v">${d.laps || 0} / ${d.stops}</span></div>
        ${stops ? `<div class="row"><span class="k">Soste</span><span class="v">${stops}</span></div>`
          : pt.duration ? `<div class="row"><span class="k">Tempo in pit lane</span><span class="v">${fmtPitTime(pt.duration)}${pt.lap ? ` (giro ${esc(pt.lap)})` : ""}</span></div>` : ""}
        ${isRace() ? `<div class="row"><span class="k">Sorpassi fatti</span><span class="v">${d.overtakes || 0}</span></div>` : ""}
        ${isRace() && cr && !d.retired ? `<div class="row"><span class="k">Mescole usate</span><span class="v">${esc(cr.used.join(", ") || "–")}${cr.wet ? " · regola sospesa (pioggia)" : cr.ok ? ' <span class="good">✓</span>' : ' <span class="warn">deve ancora cambiare</span>'}</span></div>` : ""}
        ${swTxt ? `<div class="row"><span class="k">Commissari</span><span class="v">${swTxt}</span></div>` : ""}
        ${isRace() ? `<div class="row"><span class="k">Se entra ora</span><span class="v" style="text-align:right">${exitTxt}</span></div>
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
  const radioSeen = new Set(); let radioReady = false;
  function renderRadio() {
    const ul = $("#radio-list");
    $("#radio-count").textContent = state.radio.length ? `${state.radio.length} messaggi` : "";
    const fresh = state.radio.filter((r) => !radioSeen.has(r.path));
    for (const r of fresh) {
      radioSeen.add(r.path);
      const li = document.createElement("li");
      li.className = radioReady ? "new" : "";
      li.innerHTML = `<span class="t">${localTime(r.utc)}</span><span class="who" style="color:${colourOf(r.num)}">${esc(r.tla)}</span><audio controls preload="metadata" src="/audio?p=${encodeURIComponent(r.path)}"></audio>`;
      ul.prepend(li);
      if (radioReady && prefs.autoplay && (!prefs.follow || r.num === prefs.follow || isFav(r.num))) li.querySelector("audio").play().catch(() => {});
    }
    if (!state.radio.length && !ul.children.length) ul.innerHTML = '<li class="hint">ancora nessun team radio</li>';
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
    if (list.length !== rcCount) {
      if (chimeReady && prefs.chime && list.length > rcCount) chime();
      rcCount = list.length; chimeReady = true;
    }
    const key = `${prefs.rcFilter}:${list.length}`;
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
    for (const d of state.drivers) {
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
      ctx.fillStyle = "#e8eaed"; ctx.font = `${hi ? "bold 12px" : "11px"} monospace`; ctx.fillText(d.tla, x + 9, y + 4);
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
    chartKey = ""; gapKey = ""; lapKey = "";
    if (state) { renderTelemetry(); renderGaps(); renderMap(); renderPerf(); }
  }
  $("#tabbar").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); });
  $("#rc-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    prefs.rcFilter = b.dataset.f; save("rcFilter", prefs.rcFilter);
    for (const x of $("#rc-filters").children) x.classList.toggle("on", x === b);
    rcKey = ""; incKey = ""; if (state) renderRaceControl();
  });
  for (const x of $("#rc-filters").children) x.classList.toggle("on", x.dataset.f === prefs.rcFilter);
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
