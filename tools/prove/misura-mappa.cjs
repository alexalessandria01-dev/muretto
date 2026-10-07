// Uso: replay acceso, poi PW=<percorso di playwright> node tools/prove/misura-mappa.cjs
// Misura la mappa stimata: velocità apparente (salti/blocchi) ed errore rispetto al GPS vero del replay.
const { chromium } = require(process.env.PW);
(async () => {
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1600, height: 1400 } });
  await pg.goto('http://localhost:8765/?nogps');
  await pg.waitForFunction(() => window.__mappa && Object.keys(window.__mappa.u).length > 10, null, { timeout: 60000 });
  await pg.waitForTimeout(Number(process.env.WARM || 8000));
  const S = [];
  for (let i = 0; i < Number(process.env.N || 400); i++) { S.push(await pg.evaluate(() => JSON.parse(JSON.stringify(window.__mappa)))); await pg.waitForTimeout(100); }
  const track = await pg.evaluate(() => { const m = window.__trk; return m; });
  const cal = await pg.evaluate(() => document.querySelector('#map-hint').textContent);
  await b.close();
  const total = S[0].total; let jumps = 0, steps = 0, stallRuns = 0, errs = [];
  const nums = Object.keys(S[0].u); const stall = {};
  for (let i = 1; i < S.length; i++) {
    const dt = (S[i].t - S[i - 1].t) / 1000;
    for (const n of nums) {
      const a = S[i - 1].u[n], c = S[i].u[n]; if (a == null || c == null) continue;
      const kmh = (c - a) * total / 10 / dt * 3.6; steps++;
      if (kmh > 450 || kmh < -1) { jumps++; if (jumps <= 8) console.log('salto', n, Math.round(kmh), 'km/h', 'k', S[i-1].mot[n]?.k, '->', S[i].mot[n]?.k, 'lap', S[i-1].mot[n]?.lap, '->', S[i].mot[n]?.lap, 'u', a.toFixed(3), '->', c.toFixed(3), 'campione', i); }
      if (Math.abs(kmh) < 1) { stall[n] = (stall[n] || 0) + dt; if (stall[n] >= 1.5 && stall[n] - dt < 1.5) stallRuns++; } else stall[n] = 0;
      const g = S[i].gps[n], e = S[i].xy[n];
      if (g && e && g[2] === 'OnTrack') errs.push(Math.hypot(g[0] - e[0], g[1] - e[1]) / 10);
    }
  }
  errs.sort((x, y) => x - y); const q = (p) => errs[Math.floor(errs.length * p)]?.toFixed(0);
  console.log(JSON.stringify({ hint: cal, auto: nums.length, campioni: steps, salti_oltre_450kmh: jumps, blocchi_oltre_1_5s: stallRuns, errore_m: { mediana: q(.5), p75: q(.75), p90: q(.9), max: q(.999) } }, null, 1));
})();
