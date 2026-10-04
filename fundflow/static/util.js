// Shared helpers: API client, money formatting, reference data, small UI bits.

export const S = { meta: null, accounts: [], funds: [], settings: { currency_symbol: '₱', locale: 'en-PH' } };

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export async function loadRefs() {
  const [meta, a, f] = await Promise.all([api('/meta'), api('/accounts'), api('/funds')]);
  S.meta = meta;
  S.settings = meta.settings;
  S.accounts = a.accounts;
  S.funds = f.funds;
}

export const acct = (id) => S.accounts.find((a) => a.id === Number(id));
export const fund = (id) => S.funds.find((f) => f.id === Number(id));
export const today = () => S.meta?.today || new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------- escaping

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

// ------------------------------------------------------------------ money

export function money(cents, { sign = false } = {}) {
  if (cents === null || cents === undefined) return '—';
  const abs = Math.abs(cents);
  const body = (abs / 100).toLocaleString(S.settings.locale || 'en-PH', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const prefix = cents < 0 ? '−' : sign && cents > 0 ? '+' : '';
  return `${prefix}${S.settings.currency_symbol}${body}`;
}

export function moneyShort(cents) {
  const abs = Math.abs(cents) / 100;
  const sym = S.settings.currency_symbol;
  const neg = cents < 0 ? '−' : '';
  if (abs >= 1e6) return `${neg}${sym}${(abs / 1e6).toFixed(abs >= 1e7 ? 1 : 2).replace(/\.0+$/, '')}M`;
  if (abs >= 1e4) return `${neg}${sym}${(abs / 1e3).toFixed(abs >= 1e5 ? 0 : 1).replace(/\.0$/, '')}K`;
  return money(cents);
}

/** Parse "1,234.56" into integer centavos (string math - never floats). */
export function parseAmount(raw, { allowNegative = false } = {}) {
  let s = String(raw ?? '').trim().replace(/[,\s]/g, '').replace(S.settings.currency_symbol, '').replace('₱', '');
  if (s === '') return null;
  let neg = false;
  if (s.startsWith('-') || s.startsWith('−')) { neg = true; s = s.slice(1); }
  if (!/^(\d+(\.\d{0,2})?|\.\d{1,2})$/.test(s)) return NaN;
  const [whole, frac = ''] = s.split('.');
  const cents = parseInt(whole || '0', 10) * 100 + parseInt((frac + '00').slice(0, 2), 10);
  if (neg && !allowNegative) return NaN;
  return neg ? -cents : cents;
}

export const toInput = (cents) => (cents === null || cents === undefined || cents === '' ? '' : (cents / 100).toFixed(2));

// ------------------------------------------------------------------ dates

export function fmtDate(iso, { year = true } = {}) {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(S.settings.locale || 'en-PH', { month: 'short', day: 'numeric', ...(year ? { year: 'numeric' } : {}) });
}

export function daysUntil(iso) {
  const a = new Date(today() + 'T00:00:00');
  const b = new Date(iso + 'T00:00:00');
  return Math.round((b - a) / 86400000);
}

export function relDays(iso) {
  const n = daysUntil(iso);
  if (n === 0) return 'today';
  if (n === 1) return 'tomorrow';
  if (n === -1) return 'yesterday';
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

// ----------------------------------------------------------------- colors

const PALETTE_DARK = {
  '#2a78d6': '#3987e5', '#eb6834': '#d95926', '#1baf7a': '#199e70', '#eda100': '#c98500',
  '#e87ba4': '#d55181', '#008300': '#008300', '#4a3aa7': '#9085e9', '#e34948': '#e66767',
};
export const PALETTE = Object.keys(PALETTE_DARK);

export function isDark() {
  const t = document.documentElement.dataset.theme;
  if (t) return t === 'dark';
  return matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Fund colors are stored as light-mode steps; dark mode uses the matching dark step. */
export function col(hex) {
  const h = String(hex || '#898781').toLowerCase();
  return isDark() ? PALETTE_DARK[h] || h : h;
}

/** Stable categorical color for an origin (income source etc.), by account order. */
export function originColor(item) {
  if (!item.account_id) return 'var(--muted)';
  // Income sources first, then bookkeeping (opening balances), then refunds.
  const rank = { income: 0, equity: 1, expense: 2 };
  const origins = S.accounts.filter((a) => a.kind in rank)
    .sort((x, y) => rank[x.kind] - rank[y.kind] || x.id - y.id).map((a) => a.id);
  const idx = origins.indexOf(item.account_id);
  return col(PALETTE[(idx < 0 ? 0 : idx) % PALETTE.length]);
}

// ---------------------------------------------------------------- UI bits

export const swatch = (hex) => `<span class="swatch" style="background:${esc(col(hex))}" aria-hidden="true"></span>`;

export const TYPE_LABELS = {
  income: 'Income', expense: 'Expense', allocation: 'Allocation', transfer: 'Transfer',
  payment: 'Payment', opening: 'Opening', adjustment: 'Adjustment', journal: 'Journal',
};
export const typeBadge = (t) => `<span class="badge t-${esc(t)}">${esc(TYPE_LABELS[t] || t)}</span>`;

export const KIND_LABELS = {
  amortization: 'Amortization', insurance: 'Insurance', subscription: 'Subscription', bill: 'Bill', other: 'Other',
};
export const FREQ_LABELS = { monthly: 'Monthly', quarterly: 'Quarterly', semiannual: 'Every 6 months', annual: 'Yearly' };
export const FREQ_SHORT = { monthly: '/mo', quarterly: '/qtr', semiannual: '/6 mo', annual: '/yr' };

export const STATUS = {
  overdue: ['critical', '!', 'Overdue'],
  due_today: ['serious', '!', 'Due today'],
  no_fund: ['neutral', '?', 'No fund linked'],
  saving: ['warning', '…', 'Saving up'],
  funded: ['good', '✓', 'Funded'],
  completed: ['good', '✓', 'Fully paid'],
};
export function statusPill(state) {
  const [tone, icon, label] = STATUS[state] || ['neutral', '•', state];
  return `<span class="status status-${tone}"><span class="status-icon" aria-hidden="true">${icon}</span>${esc(label)}</span>`;
}

export function meter(value, max, tone = '') {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return `<div class="meter ${tone}" role="meter" aria-valuemin="0" aria-valuemax="${max}" aria-valuenow="${value}"><span style="width:${pct.toFixed(1)}%"></span></div>`;
}

/** Horizontal stacked bar: items [{label, amount, color}] - 2px surface gaps between segments. */
export function stackbar(items, { total } = {}) {
  const pos = items.filter((i) => i.amount > 0);
  const sum = total || pos.reduce((s, i) => s + i.amount, 0);
  if (!sum) return '<div class="stackbar"><span style="flex:1;background:var(--track)"></span></div>';
  return `<div class="stackbar">${pos.map((i) => {
    const pct = (i.amount / sum) * 100;
    return `<span style="flex:${i.amount} 1 0;background:${esc(i.color)}" data-tip="${esc(`${i.label}: ${money(i.amount)} (${pct.toFixed(pct < 10 ? 1 : 0)}%)`)}"></span>`;
  }).join('')}</div>`;
}

export function legend(items) {
  return `<div class="legend">${items.map((i) => `<span class="legend-item"><span class="swatch" style="background:${esc(i.color)}"></span>${esc(i.label)}${i.value ? ` <span class="muted">${esc(i.value)}</span>` : ''}</span>`).join('')}</div>`;
}

// ---------------------------------------------------------------- toasts

export function toast(msg, { error = false } = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (error ? ' error' : '');
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), error ? 6000 : 3000);
}

// --------------------------------------------------------------- tooltip

export function initTooltips() {
  const tip = document.getElementById('tooltip');
  const show = (e) => {
    const el = e.target.closest?.('[data-tip]');
    if (!el) { tip.hidden = true; return; }
    tip.textContent = el.getAttribute('data-tip');
    tip.hidden = false;
    const pad = 14;
    const r = tip.getBoundingClientRect();
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  };
  document.addEventListener('mousemove', show);
  document.addEventListener('mouseleave', () => { tip.hidden = true; });
  document.addEventListener('scroll', () => { tip.hidden = true; }, true);
}
