function getCookie(name) {
  return document.cookie.split('; ').reduce((acc, c) => {
    const i = c.indexOf('=');
    const k = i === -1 ? c : c.slice(0, i);
    const v = i === -1 ? '' : c.slice(i + 1);
    return k === name ? decodeURIComponent(v) : acc;
  }, '');
}

function authFetch(url, opts = {}) {
  opts.headers = Object.assign({}, opts.headers || {});
  const m = (opts.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(m)) {
    opts.headers['X-CSRF-Token'] = getCookie('ac_csrf');
  }
  return fetch(url, opts);
}

let state = { power: 'off', mode: 'cool', temp: 68, fan: 'auto', ambient: null };

async function refresh() {
  try {
    const r = await authFetch('/api/status');
    if (!r.ok) throw new Error(await r.text());
    state = await r.json();
    render();
    setStatus('ok');
    hideError();
  } catch (e) {
    setStatus('err');
    showError('Cannot reach server: ' + e.message);
  }
}

async function send(payload) {
  Object.assign(state, payload);
  render();
  try {
    const r = await authFetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const msg = await r.json().catch(() => ({ detail: r.statusText }));
      showError('Error: ' + (msg.detail || r.statusText));
      refresh();
    }
  } catch (e) {
    showError('Network error: ' + e.message);
    refresh();
  }
}

function togglePower() { send({ power: state.power === 'on' ? 'off' : 'on' }); }
function adjustTemp(d) {
  const t = Math.min(86, Math.max(60, state.temp + d));
  if (t !== state.temp) send({ temp: t });
}
function setMode(m) { send({ mode: m }); }
function setFan(f)  { send({ fan: f }); }

function render() {
  const on = state.power === 'on';
  document.getElementById('power-btn').classList.toggle('on', on);
  document.getElementById('power-label').textContent = on ? 'ON' : 'OFF';
  const td = document.getElementById('temp-display');
  td.textContent = '';
  td.appendChild(document.createTextNode(state.temp));
  const sup = document.createElement('span');
  sup.textContent = '°F';
  td.appendChild(sup);
  document.getElementById('btn-minus').disabled = state.temp <= 60;
  document.getElementById('btn-plus').disabled  = state.temp >= 86;
  document.getElementById('ambient').textContent =
    state.ambient != null ? 'Room: ' + state.ambient + '°F' : '';
  document.querySelectorAll('.chip[data-mode]').forEach(el =>
    el.classList.toggle('active', el.dataset.mode === state.mode));
  document.querySelectorAll('.chip[data-fan]').forEach(el =>
    el.classList.toggle('active', el.dataset.fan === state.fan));
}

function setStatus(s) {
  document.getElementById('status-dot').className = s;
  document.getElementById('status-text').textContent = s === 'ok' ? 'Connected' : 'Disconnected';
}
function showError(msg) {
  const b = document.getElementById('error-banner');
  b.textContent = msg; b.style.display = 'block';
}
function hideError() {
  document.getElementById('error-banner').style.display = 'none';
}

// ── Sleep timer ──────────────────────────────────────────────
let timerEndsAt = null;
let timerTick = null;

function toggleTimerBody() {
  document.getElementById('timer-body').classList.toggle('open');
}

async function applyTimer(minutes) {
  try {
    const r = await authFetch('/api/timer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes }),
    });
    if (!r.ok) throw new Error(await r.text());
    renderTimer(await r.json());
    document.getElementById('timer-body').classList.remove('open');
    document.getElementById('timer-minutes').value = '';
  } catch (e) { showError('Timer: ' + e.message); }
}

async function applyCustomTimer() {
  const val = parseInt(document.getElementById('timer-minutes').value, 10);
  if (!val || val < 1 || val > 1440) return;
  await applyTimer(val);
}

async function cancelTimer(e) {
  if (e && e.stopPropagation) e.stopPropagation();
  try {
    const r = await authFetch('/api/timer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: null }),
    });
    renderTimer(await r.json());
    document.getElementById('timer-body').classList.remove('open');
  } catch (e) { showError('Timer: ' + e.message); }
}

async function checkTimer() {
  try {
    const r = await authFetch('/api/timer');
    renderTimer(await r.json());
  } catch (e) {}
}

function renderTimer(data) {
  const cancelBtn = document.getElementById('timer-cancel');
  const countdown = document.getElementById('timer-countdown');
  if (data.active) {
    timerEndsAt = new Date(data.ends_at);
    cancelBtn.style.display = 'inline-block';
    if (!timerTick) timerTick = setInterval(tickCountdown, 1000);
    tickCountdown();
  } else {
    timerEndsAt = null;
    cancelBtn.style.display = 'none';
    countdown.textContent = '';
    if (timerTick) { clearInterval(timerTick); timerTick = null; }
  }
}

function tickCountdown() {
  if (!timerEndsAt) return;
  const rem = Math.max(0, Math.round((timerEndsAt - Date.now()) / 1000));
  const h = Math.floor(rem / 3600);
  const m = Math.floor((rem % 3600) / 60);
  let txt;
  if (h > 0)      txt = `Off in ${h}h ${m}m`;
  else if (m > 1) txt = `Off in ${m}m`;
  else            txt = `Off in ${rem}s`;
  document.getElementById('timer-countdown').textContent = txt;
  if (rem === 0) {
    clearInterval(timerTick); timerTick = null; timerEndsAt = null;
    document.getElementById('timer-cancel').style.display = 'none';
    document.getElementById('timer-countdown').textContent = '';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-action]').forEach(el => {
    const a = el.dataset.action;
    if (a === 'power')               el.addEventListener('click', togglePower);
    else if (a === 'temp-down')      el.addEventListener('click', () => adjustTemp(-1));
    else if (a === 'temp-up')        el.addEventListener('click', () => adjustTemp(+1));
    else if (a === 'mode')           el.addEventListener('click', () => setMode(el.dataset.mode));
    else if (a === 'fan')            el.addEventListener('click', () => setFan(el.dataset.fan));
    else if (a === 'timer-toggle')   el.addEventListener('click', toggleTimerBody);
    else if (a === 'timer-cancel')   el.addEventListener('click', cancelTimer);
    else if (a === 'timer-preset')   el.addEventListener('click', () => applyTimer(parseInt(el.dataset.min, 10)));
    else if (a === 'timer-set')      el.addEventListener('click', applyCustomTimer);
  });
  const tm = document.getElementById('timer-minutes');
  if (tm) tm.addEventListener('keydown', e => {
    if (e.key === 'Enter') applyCustomTimer();
  });
  refresh();
  checkTimer();
  setInterval(refresh, 30000);
});
