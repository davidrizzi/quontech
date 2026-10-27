/* QuonTech - daily performance tracker.
   Reads data/portfolio_performance.json (written by the GitHub Action) and fills
   the tracker block and the per-round table. Shows nothing it cannot verify. */
(() => {
  'use strict';
  const root = document.querySelector('[data-quontech-performance]');
  if (!root) return;
  const $ = (sel, el = root) => el.querySelector(sel);
  const chartEl = $('[data-qt-chart]');
  const statusEl = $('[data-qt-status]');

  const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  const signed = (x, digits = 2) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toFixed(digits)}`;
  const pct = x => `${signed(x)}%`;
  const ratio = x => signed(x);
  const plainPct = x => `${x.toFixed(2)}%`;
  const fmtDate = (iso, withYear = true) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-GB',
    withYear ? { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }
             : { day: 'numeric', month: 'short', timeZone: 'UTC' });
  const NA_TITLE = 'Shown once at least five daily returns are available';

  function setValue(el, value, formatter) {
    if (!el) return;
    if (value === null || value === undefined || !Number.isFinite(value)) {
      el.textContent = 'n/a';
      el.title = NA_TITLE;
      el.classList.add('is-empty');
    } else {
      el.textContent = formatter(value);
      el.removeAttribute('title');
      el.classList.remove('is-empty');
    }
  }

  /* ---------------- table: one row per round, filled only when final ---------------- */
  function fillTable(metrics) {
    const rows = document.querySelectorAll('[data-qt-milestone]');
    const byKey = {};
    (metrics.milestones || []).forEach(m => { byKey[m.key] = m; });
    rows.forEach(tr => {
      const m = byKey[tr.dataset.qtMilestone];
      if (!m || m.status !== 'final') return;
      const cells = tr.querySelectorAll('td');
      if (cells.length < 3) return;
      [[m.return_pct, pct], [m.volatility_pct, plainPct], [m.sharpe, ratio]].forEach(([v, f], i) => {
        cells[i].classList.remove('pending');
        setValue(cells[i], v, f);
      });
      const th = tr.querySelector('th');
      if (th && !th.querySelector('.qt-asof')) {
        const s = document.createElement('span');
        s.className = 'qt-asof';
        s.textContent = `${fmtDate(m.start_close, false)} to ${fmtDate(m.as_of_close)}`;
        th.appendChild(s);
      }
    });
  }

  /* ---------------- chart: NAV indexed to 100, crosshair + tooltip ---------------- */
  let series = [];
  function niceTicks(lo, hi, count = 4) {
    const span = hi - lo;
    const raw = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(k => k * mag).find(s => s >= raw) || raw;
    const ticks = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
    return { ticks, step };
  }

  function draw() {
    if (!series.length) return;
    const W = Math.max(260, Math.floor(chartEl.getBoundingClientRect().width));
    const H = Math.floor(chartEl.getBoundingClientRect().height) || 260;
    const m = { l: 52, r: 16, t: 14, b: 30 };
    const vals = series.map(r => r.index_100);
    let lo = Math.min(100, ...vals), hi = Math.max(100, ...vals);
    const pad = Math.max(0.15, (hi - lo) * 0.15);
    lo -= pad; hi += pad;
    const { ticks } = niceTicks(lo, hi);
    lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
    const n = series.length;
    const x = i => m.l + (n === 1 ? 0.5 : i / (n - 1)) * (W - m.l - m.r);
    const y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b);
    const decimals = (hi - lo) < 2 ? 1 : 0;

    let s = '';
    ticks.forEach(v => {
      s += `<line class="qt-grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      s += `<text class="qt-axis" x="${m.l - 10}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(decimals)}</text>`;
    });
    s += `<line class="qt-base" x1="${m.l}" x2="${W - m.r}" y1="${y(100)}" y2="${y(100)}"/>`;
    s += `<text class="qt-axis qt-base-label" x="${W - m.r}" y="${y(100) - 6}" text-anchor="end">16 Oct = 100</text>`;
    const d = series.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(r.index_100).toFixed(1)}`).join(' ');
    s += `<path class="qt-line" d="${d}"/>`;
    s += `<circle class="qt-end" cx="${x(n - 1)}" cy="${y(vals[n - 1])}" r="4"/>`;
    s += `<text class="qt-axis" x="${m.l}" y="${H - 8}" text-anchor="start">${fmtDate(series[0].date, false)}</text>`;
    if (n > 1) s += `<text class="qt-axis" x="${W - m.r}" y="${H - 8}" text-anchor="end">${fmtDate(series[n - 1].date, false)}</text>`;
    s += `<g class="qt-hover" visibility="hidden"><line class="qt-cross" y1="${m.t}" y2="${H - m.b}"/><circle class="qt-dot" r="5"/></g>`;
    s += `<rect class="qt-hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}"/>`;

    chartEl.innerHTML = `<svg class="qt-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Portfolio value indexed to 100 on 16 October 2026, daily from ${fmtDate(series[0].date)} to ${fmtDate(series[n - 1].date)}">${s}</svg>
      <div class="qt-tip" hidden></div>`;

    const svg = chartEl.querySelector('svg');
    const hover = svg.querySelector('.qt-hover');
    const cross = svg.querySelector('.qt-cross');
    const dot = svg.querySelector('.qt-dot');
    const tip = chartEl.querySelector('.qt-tip');
    const show = clientX => {
      const box = svg.getBoundingClientRect();
      const px = clientX - box.left;
      const i = Math.max(0, Math.min(n - 1, Math.round((px - m.l) / ((W - m.l - m.r) / Math.max(1, n - 1)))));
      const r = series[i];
      const cx = x(i), cy = y(r.index_100);
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
      dot.setAttribute('cx', cx); dot.setAttribute('cy', cy);
      hover.setAttribute('visibility', 'visible');
      tip.innerHTML = `<strong>${fmtDate(r.date)}</strong>
        <span><em>Value</em>${usd.format(r.nav_usd)}</span>
        <span><em>Since 16 Oct</em>${pct(r.return_pct)}</span>
        <span><em>Day</em>${pct(r.daily_return_pct)}</span>`;
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.min(Math.max(cx - tw / 2, 0), W - tw)}px`;
      tip.style.top = `${Math.max(0, cy - tip.offsetHeight - 14)}px`;
    };
    const hide = () => { hover.setAttribute('visibility', 'hidden'); tip.hidden = true; };
    const hit = svg.querySelector('.qt-hit');
    hit.addEventListener('pointermove', e => show(e.clientX));
    hit.addEventListener('pointerdown', e => show(e.clientX));
    hit.addEventListener('pointerleave', hide);
  }

  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 120); });

  /* ---------------- load ---------------- */
  const url = root.dataset.qtSource || 'data/portfolio_performance.json';
  fetch(`${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`, { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(data => {
      if (data.status !== 'validated_eod' || !Array.isArray(data.series) || !data.series.length) {
        statusEl.textContent = 'Awaiting the first verified US close';
        return;
      }
      const last = data.series[data.series.length - 1];
      const t = data.metrics && data.metrics.to_date;
      if (data.as_of !== last.date || !Number.isFinite(last.nav_usd) || !t || t.as_of_close !== last.date) {
        throw new Error('Inconsistent NAV file');
      }
      root.classList.add('is-live');
      setValue($('[data-qt-return]'), t.return_pct, pct);
      setValue($('[data-qt-nav]'), last.nav_usd, v => usd.format(v));
      setValue($('[data-qt-vol]'), t.volatility_pct, plainPct);
      setValue($('[data-qt-sharpe]'), t.sharpe, ratio);
      const obs = $('[data-qt-obs]');
      if (obs) obs.textContent = `${t.n_returns} daily return${t.n_returns === 1 ? '' : 's'}`;
      const frozen = data.holdings_status === 'frozen_awaiting_next_period';
      statusEl.textContent = frozen
        ? `Last close ${fmtDate(last.date)} · next round's positions not yet loaded`
        : `Last verified US close · ${fmtDate(last.date)}`;
      series = data.series;
      draw();
      fillTable(data.metrics);
    })
    .catch(err => {
      console.error('QuonTech tracker:', err);
      statusEl.textContent = 'Data currently unavailable';
      statusEl.classList.add('is-error');
    });
})();
