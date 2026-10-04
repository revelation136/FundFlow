// FundFlow single-page app: router + views.
import { renderSankey } from './charts.js';
import {
  deleteRecord, duplicateTxn, editTxn, onSaved, openAccountModal, openAdjustAllocation, openFundModal,
  openFundSetAsides, openObligationModal, openReconcile, openTxnDetail, openTxnModal, restoreTxn, voidTxn,
} from './forms.js';
import {
  FREQ_LABELS, FREQ_SHORT, KIND_LABELS, S, TYPE_LABELS, acct, api, col, esc, fmtDate, fund, initTooltips, legend,
  loadRefs, meter, money, originColor, relDays, stackbar, statusPill, swatch, toast, today, typeBadge,
} from './util.js';

const main = document.getElementById('main');
const HOLDING = ['asset', 'liability'];
let lastPlan = null;

// ----------------------------------------------------------------- helpers

const uniq = (xs) => [...new Set(xs)];
const dataAttrs = (d = {}) => Object.entries(d).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');

/** Small icon button that triggers an action. */
const iconBtn = (action, data, label, glyph = '✎') =>
  `<button type="button" class="icon-btn" data-action="${action}"${dataAttrs(data)} aria-label="${esc(label)}" title="${esc(label)}">${glyph}</button>`;

/** "⋯" dropdown of actions. Items: {label, action, data, danger} | '-' (separator) | falsy (skipped). */
function menu(items, label = 'More actions') {
  const list = items.filter(Boolean);
  const clean = list.filter((item, i) => item !== '-' || (i > 0 && i < list.length - 1 && list[i - 1] !== '-'));
  const html = clean.map((i) => (i === '-' ? '<div class="menu-sep" role="separator"></div>'
    : `<button type="button" role="menuitem" class="menu-item${i.danger ? ' danger' : ''}" data-action="${i.action}"${dataAttrs(i.data)}>${esc(i.label)}</button>`)).join('');
  return `<details class="menu"><summary class="icon-btn" aria-label="${esc(label)}" title="${esc(label)}">⋯</summary><div class="menu-pop" role="menu">${html}</div></details>`;
}

function fundMenu(id) {
  const f = fund(id);
  if (!f) return '';
  return menu([
    { label: 'Edit fund', action: 'edit-fund', data: { id } },
    { label: 'Add money to this fund', action: 'new-txn', data: { type: 'allocation', 'to-fund': id } },
    { label: 'Move money out of it', action: 'new-txn', data: { type: 'allocation', 'from-fund': id } },
    { label: 'Adjust amount in an account', action: 'adjust-allocation', data: { fund: id } },
    { label: 'Record expense from it', action: 'new-txn', data: { type: 'expense', fund: id } },
    { label: 'View history', action: 'go', data: { href: `#/funds/${id}` } },
    '-',
    !f.is_system && { label: f.archived ? 'Restore fund' : 'Archive fund', action: 'archive-fund', data: { id, archived: f.archived } },
    !f.is_system && { label: 'Delete fund…', action: 'delete-fund', data: { id }, danger: true },
  ], `Actions for ${f.name}`);
}

function accountMenu(id) {
  const a = acct(id);
  if (!a) return '';
  const holding = HOLDING.includes(a.kind);
  const noun = holding ? 'account' : a.kind === 'income' ? 'source' : a.kind === 'expense' ? 'category' : 'account';
  return menu([
    { label: `Edit ${noun}`, action: 'edit-account', data: { id } },
    holding && { label: 'Reconcile balance…', action: 'reconcile-account', data: { id } },
    holding && a.kind === 'asset' && { label: 'Adjust fund split', action: 'adjust-allocation', data: { account: id } },
    holding && { label: 'Record income into it', action: 'new-txn', data: { type: 'income', account: id } },
    holding && { label: 'Record expense from it', action: 'new-txn', data: { type: 'expense', account: id } },
    holding && { label: 'Transfer from it', action: 'new-txn', data: { type: 'transfer', 'from-account': id } },
    a.kind === 'income' && { label: 'Record income from it', action: 'new-txn', data: { type: 'income', source: id } },
    a.kind === 'expense' && { label: 'Record expense in it', action: 'new-txn', data: { type: 'expense', category: id } },
    { label: 'View history', action: 'go', data: { href: `#/accounts/${id}` } },
    '-',
    !a.is_system && { label: a.archived ? 'Restore' : 'Archive', action: 'archive-account', data: { id, archived: a.archived } },
    !a.is_system && { label: 'Delete…', action: 'delete-account', data: { id }, danger: true },
  ], `Actions for ${a.name}`);
}

function obligationMenu(o) {
  return menu([
    !o.completed && !o.archived && { label: 'Record payment', action: 'pay-obligation', data: { id: o.id, name: o.name } },
    { label: 'Edit obligation', action: 'edit-obligation', data: { id: o.id } },
    !o.archived && { label: 'Schedule & payments', action: 'obligation-schedule', data: { id: o.id } },
    o.fund_id && { label: 'Open sinking fund', action: 'go', data: { href: `#/funds/${o.fund_id}` } },
    '-',
    { label: o.archived ? 'Restore' : 'Archive', action: 'archive-obligation', data: { id: o.id, archived: o.archived ? 1 : 0 } },
    { label: 'Delete…', action: 'delete-obligation', data: { id: o.id }, danger: true },
  ], `Actions for ${o.name}`);
}

function txnMenu(t) {
  const editable = t.type !== 'journal';
  return menu([
    !t.voided_at && editable && { label: 'Edit', action: 'edit-txn', data: { id: t.id } },
    editable && { label: 'Duplicate', action: 'duplicate-txn', data: { id: t.id } },
    { label: 'View details & history', action: 'open-txn', data: { id: t.id } },
    '-',
    t.voided_at
      ? { label: 'Restore (un-void)', action: 'restore-txn', data: { id: t.id } }
      : { label: 'Void…', action: 'void-txn', data: { id: t.id }, danger: true },
  ], 'Transaction actions');
}

function flowText(t) {
  const neg = t.postings.filter((p) => p.amount < 0);
  const pos = t.postings.filter((p) => p.amount > 0);
  const holdNeg = neg.filter((p) => HOLDING.includes(p.account_kind));
  const holdPos = pos.filter((p) => HOLDING.includes(p.account_kind));
  const arrow = '<span class="arrow">→</span>';
  const label = (p) => (HOLDING.includes(p.account_kind) ? `${p.account_name} · ${p.fund_name}` : p.account_name);
  try {
    switch (t.type) {
      case 'allocation':
        return `${esc(uniq(neg.map((p) => p.fund_name)).join(', '))}${arrow}${esc(uniq(pos.map((p) => p.fund_name)).join(', '))} <span class="muted">in ${esc(neg[0].account_name)}</span>`;
      case 'income':
        return `${esc(neg[0].account_name)}${arrow}${esc(pos[0].account_name)} · ${esc(uniq(pos.map((p) => p.fund_name)).join(', '))}`;
      case 'expense':
      case 'payment':
        return `${esc(holdNeg[0].account_name)} · ${esc(holdNeg[0].fund_name)}${arrow}${esc(uniq(pos.map((p) => p.account_name)).join(', '))}`;
      case 'transfer':
        return `${esc(holdNeg[0].account_name)}${arrow}${esc(holdPos[0].account_name)} · ${esc(holdNeg[0].fund_name)}`;
      default:
        return `${esc(uniq(neg.map(label)).join(', '))}${arrow}${esc(uniq(pos.map(label)).join(', '))}`;
    }
  } catch {
    return '';
  }
}

function txnRows(items, { delta = false, running = false, origins = false } = {}) {
  if (!items.length) return `<tr><td colspan="${running ? 7 : 6}" class="empty">No transactions yet.</td></tr>`;
  return items.map((t) => {
    const amount = delta ? `<span class="${t.delta < 0 ? 'neg' : t.delta > 0 ? 'pos' : ''}">${esc(money(t.delta, { sign: true }))}</span>` : esc(money(t.amount));
    const note = origins && t.origins?.length
      ? `<div class="origin-note">Funded by ${esc(t.origins.map((o) => `${o.label} ${Math.round(o.share * 100)}%`).join(' · '))}</div>` : '';
    const quick = t.voided_at
      ? iconBtn('restore-txn', { id: t.id }, 'Restore', '↺')
      : t.type !== 'journal' ? iconBtn('edit-txn', { id: t.id }, 'Edit transaction') : '';
    return `<tr class="clickable ${t.voided_at ? 'voided' : ''}" data-action="open-txn" data-id="${t.id}">
      <td class="nowrap">${esc(fmtDate(t.date))}</td>
      <td>${typeBadge(t.type)}</td>
      <td>${esc(t.description || TYPE_LABELS[t.type])}${note}</td>
      <td class="flow-cell">${flowText(t)}</td>
      <td class="num">${amount}</td>
      ${running ? `<td class="num">${esc(money(t.running))}</td>` : ''}
      <td class="actions-cell">${quick}${txnMenu(t)}</td>
    </tr>`;
  }).join('');
}

function txnTable(items, opts = {}) {
  return `<div class="table-wrap"><table>
    <thead><tr><th>Date</th><th>Type</th><th>Description</th><th>Flow</th><th class="num">Amount</th>${opts.running ? '<th class="num">Balance</th>' : ''}<th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${txnRows(items, opts)}</tbody></table></div>`;
}

const pageHead = (title, { sub = '', actions = '', crumb = '' } = {}) => `<div class="page-head">
  <div>${crumb ? `<div class="crumb">${crumb}</div>` : ''}<h1>${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ''}</div>
  <div class="actions">${actions}</div></div>`;

const stat = (label, value, note = '', cls = '', href = '') => `<div class="stat ${cls}"${href ? ` data-href="${href}"` : ''}><div class="stat-label">${esc(label)}</div>
  <div class="stat-value">${value}</div>${note ? `<div class="stat-note">${note}</div>` : ''}</div>`;

function fundSegments(items) {
  return items.map((i) => ({ label: i.fund_name, amount: i.amount, color: col(fund(i.fund_id)?.color) }));
}

/** A money amount that opens "Adjust allocation" for one account × fund cell. */
const cellBtn = (accountId, fundId, amount, label) =>
  `<button type="button" class="cell-btn num ${amount < 0 ? 'neg' : ''}" data-action="adjust-allocation" data-account="${accountId}" data-fund="${fundId}" title="Adjust ${esc(label)}">${amount ? esc(money(amount)) : '<span class="muted">—</span>'}</button>`;

// --------------------------------------------------------------- dashboard

async function viewDashboard() {
  const d = await api('/dashboard');
  const p = d.plan;
  lastPlan = null;
  const hasAccounts = S.accounts.some((a) => HOLDING.includes(a.kind));
  const hasTxns = d.recent.length > 0;
  const hasFunds = S.funds.length > 1;
  const hasObligations = p.totals.monthly_commitment > 0;
  const onboarding = !(hasAccounts && hasTxns && hasFunds) ? `<div class="card">
      <div class="card-head"><h2>Get started</h2><span class="muted">about 5 minutes</span></div>
      <ol class="steps">
        <li class="${hasAccounts ? 'done' : ''}">Add where your money sits — bank accounts, e-wallets, cash. <a href="#/accounts" data-action="new-account">Add account</a></li>
        <li class="${hasFunds ? 'done' : ''}">Create funds for each purpose — e.g. “Fund 1”, “Solar Project”, “Emergency Fund”. <a href="#/funds" data-action="new-fund">New fund</a></li>
        <li class="${hasTxns ? 'done' : ''}">Record what you already have (opening balance) or your latest salary. <a href="#/" data-action="new-txn" data-type="opening">Opening balance</a> · <a href="#/" data-action="new-txn" data-type="income">Income</a></li>
        <li>Allocate money from a fund to another — e.g. ₱50,000 of Fund 1 to Solar Project. <a href="#/" data-action="new-txn" data-type="allocation">Allocate</a></li>
        <li class="${hasObligations ? 'done' : ''}">Add amortizations and insurance to see what to save each month. <a href="#/plan" data-action="new-obligation">Add obligation</a></li>
      </ol></div>` : '';

  const holding = d.accounts;
  const cellsBy = (aid) => d.cells.filter((c) => c.account_id === aid);
  const accountList = holding.length ? holding.map((a) => {
    const segs = cellsBy(a.id).filter((c) => c.amount > 0).map((c) => ({ fund_id: c.fund_id, fund_name: fund(c.fund_id)?.name, amount: c.amount }));
    const owed = a.kind === 'liability';
    return `<div class="list-item stacked" data-href="#/accounts/${a.id}">
      <div class="row between"><a class="title" href="#/accounts/${a.id}">${esc(a.name)}${owed ? ' <span class="muted small">owed</span>' : ''}</a>
      <span class="row"><span class="num ${owed && a.balance > 0 ? 'neg' : ''}">${esc(money(a.balance))}</span>${accountMenu(a.id)}</span></div>
      ${owed ? '' : stackbar(fundSegments(segs))}</div>`;
  }).join('') : '<p class="empty">No accounts yet. <a href="#/accounts" data-action="new-account">Add one</a></p>';

  const funds = d.funds.filter((f) => !f.archived);
  const fundList = funds.map((f) => `<div class="list-item stacked" data-href="#/funds/${f.id}">
      <div class="row between"><a class="row title" href="#/funds/${f.id}">${swatch(f.color)}${esc(f.name)}</a>
        <span class="row"><span class="num ${f.balance < 0 ? 'neg' : ''}">${esc(money(f.balance))}</span>${fundMenu(f.id)}</span></div>
      ${f.target ? `<div class="meter-row">${meter(f.balance, f.target, f.balance >= f.target ? 'good' : '')}<span>${Math.round((f.balance / f.target) * 100)}% of ${esc(money(f.target))}</span></div>` : ''}
    </div>`).join('');

  // Allocation matrix: rows = accounts, columns = funds with money. Every cell is editable.
  const matrixFunds = funds.filter((f) => d.cells.some((c) => c.fund_id === f.id));
  const matrix = holding.length && matrixFunds.length ? `<div class="card">
    <div class="card-head"><h2>Allocation matrix</h2><span class="muted">click any amount to adjust it</span></div>
    <div class="table-wrap"><table class="matrix">
      <thead><tr><th>Account</th>${matrixFunds.map((f) => `<th class="num"><span class="row" style="justify-content:flex-end">${swatch(f.color)}${esc(f.name)}</span></th>`).join('')}<th class="num">Total</th></tr></thead>
      <tbody>${holding.map((a) => `<tr><td><a href="#/accounts/${a.id}">${esc(a.name)}</a></td>${matrixFunds.map((f) => {
        const c = d.cells.find((x) => x.account_id === a.id && x.fund_id === f.id);
        return `<td class="num">${a.kind === 'asset' ? cellBtn(a.id, f.id, c ? c.amount : 0, `${f.name} in ${a.name}`) : c ? `<span class="${c.amount < 0 ? 'neg' : ''}">${esc(money(c.amount))}</span>` : '<span class="muted">—</span>'}</td>`;
      }).join('')}<td class="num"><b>${esc(money(cellsBy(a.id).reduce((s, c) => s + c.amount, 0)))}</b></td></tr>`).join('')}</tbody>
      <tfoot><tr><td>Fund total</td>${matrixFunds.map((f) => `<td class="num">${esc(money(f.balance))}</td>`).join('')}<td class="num">${esc(money(d.net_worth))}</td></tr></tfoot>
    </table></div><p class="small muted mt">Credit card and loan rows are negative: money owed reduces the fund it was charged to.</p></div>` : '';

  const upcoming = p.upcoming.length ? p.upcoming.map((u) => `<div class="list-item">
      <div class="grow"><a class="title" href="#/plan">${esc(u.name)}</a><div class="small muted">${esc(fmtDate(u.date))} · ${esc(relDays(u.date))}${u.of ? ` · #${u.number} of ${u.of}` : ''}</div></div>
      <span class="num ${u.overdue ? 'neg' : ''}">${esc(money(u.amount))}</span>
      <button type="button" class="btn btn-sm" data-action="pay-obligation" data-id="${u.obligation_id}" data-name="${esc(u.name)}">Pay</button></div>`).join('')
    : '<p class="empty">No upcoming payments. <a href="#/plan" data-action="new-obligation">Add an obligation</a></p>';

  const shortfall = p.totals.shortfall_now > 0
    ? `<p class="callout warn">${statusPill('overdue')} ${esc(money(p.totals.shortfall_now))} is due now but not covered by its sinking funds. <a href="#/plan">Review obligations</a></p>` : '';

  return `${pageHead('Dashboard', {
    sub: fmtDate(today()),
    actions: '<button class="btn" data-action="new-txn" data-type="income">+ Income</button><button class="btn" data-action="new-txn" data-type="allocation">Allocate</button><button class="btn" data-action="new-txn" data-type="expense">+ Expense</button>',
  })}
  <div class="stack">
    ${onboarding}
    <div class="card"><div class="hero">
      <div><div class="stat-label">Net worth</div><div class="hero-figure">${esc(money(d.net_worth))}</div></div>
      <div><div class="stat-label">In accounts</div><div class="stat-value">${esc(money(d.assets))}</div></div>
      <div><div class="stat-label">Owed</div><div class="stat-value">${esc(money(d.liabilities))}</div></div>
      <div><div class="stat-label">Income this month</div><div class="stat-value">${esc(money(d.month.income))}</div></div>
      <div><div class="stat-label">Spent this month</div><div class="stat-value">${esc(money(d.month.expense))}</div></div>
    </div></div>
    <div class="stats">
      ${stat('Free to spend', esc(money(p.free_to_spend)), 'Unallocated after this month’s set-asides', 'emph', '#/plan')}
      ${stat('Set aside this month', esc(money(p.totals.set_aside_this_month)), 'for amortizations & insurance', '', '#/plan')}
      ${stat('Due in next 30 days', esc(money(p.totals.due_next_30_days)), '', '', '#/plan')}
      ${stat('Monthly commitments', esc(money(p.totals.monthly_commitment)), 'average per month', '', '#/plan')}
    </div>
    ${shortfall}
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Funds</h2><span class="row"><button class="btn btn-sm" data-action="new-fund">+ Fund</button><a href="#/funds">All funds</a></span></div><div class="list">${fundList}</div></div>
      <div class="card"><div class="card-head"><h2>Accounts</h2><span class="row"><button class="btn btn-sm" data-action="new-account">+ Account</button><a href="#/accounts">All accounts</a></span></div><div class="list">${accountList}</div>
        ${holding.length ? legend(funds.filter((f) => d.cells.some((c) => c.fund_id === f.id && c.amount > 0)).map((f) => ({ label: f.name, color: col(f.color) }))) : ''}</div>
    </div>
    ${matrix}
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Upcoming payments</h2><a href="#/plan">Obligations</a></div><div class="list">${upcoming}</div></div>
      <div class="card"><div class="card-head"><h2>Recent activity</h2><a href="#/ledger">Ledger</a></div>
        <div class="list">${d.recent.length ? d.recent.map((t) => `<div class="list-item clickable" data-action="open-txn" data-id="${t.id}">
          <div class="grow"><div class="title">${esc(t.description || TYPE_LABELS[t.type])}</div><div class="small muted flow-cell">${esc(fmtDate(t.date, { year: false }))} · ${flowText(t)}</div></div>
          <span class="num">${esc(money(t.amount))}</span>${txnMenu(t)}</div>`).join('') : '<p class="empty">Nothing recorded yet.</p>'}</div></div>
    </div>
  </div>`;
}

// ------------------------------------------------------------------- funds

async function viewFunds() {
  const { funds } = await api('/funds');
  const active = funds.filter((f) => !f.archived);
  const archived = funds.filter((f) => f.archived);
  const card = (f) => `<div class="card fund-card" data-href="#/funds/${f.id}">
    <div class="row between"><a class="name" href="#/funds/${f.id}">${swatch(f.color)}${esc(f.name)}${f.is_system ? ' <span class="badge">system</span>' : ''}${f.archived ? ' <span class="badge">archived</span>' : ''}</a>
      <span class="row">${iconBtn('edit-fund', { id: f.id }, `Edit ${f.name}`)}${fundMenu(f.id)}</span></div>
    <div class="bal num ${f.balance < 0 ? 'neg' : ''}">${esc(money(f.balance))}</div>
    ${f.target ? `<div class="meter-row">${meter(f.balance, f.target, f.balance >= f.target ? 'good' : '')}<span>${Math.round((f.balance / f.target) * 100)}% of ${esc(money(f.target))} target</span></div>` : ''}
    ${f.description ? `<p class="small muted">${esc(f.description)}</p>` : ''}
    <div class="chips">${f.by_account.map((b) => `<button type="button" class="chip" data-action="adjust-allocation" data-account="${b.account_id}" data-fund="${f.id}" title="Adjust ${esc(f.name)} in ${esc(b.account_name)}">${esc(b.account_name)} ${esc(money(b.amount))}</button>`).join('')}</div>
  </div>`;
  return `${pageHead('Funds', {
    sub: 'Purposes for your money. One bank account can hold many funds. Click an account chip to adjust that fund’s share of it.',
    actions: '<button class="btn" data-action="adjust-allocation">Adjust allocation</button><button class="btn" data-action="new-txn" data-type="allocation">Allocate</button><button class="btn btn-primary" data-action="new-fund">+ New fund</button>',
  })}
  <div class="grid grid-auto">${active.map(card).join('')}</div>
  ${archived.length ? `<h2 class="mt">Archived</h2><div class="grid grid-auto mt">${archived.map(card).join('')}</div>` : ''}`;
}

async function viewFundDetail(id) {
  const d = await api(`/funds/${id}`);
  const f = d.fund;
  const totalIn = d.inflows.reduce((s, x) => s + x.amount, 0);
  const totalOut = d.outflows.reduce((s, x) => s + x.amount, 0);
  const comp = d.composition.filter((c) => c.amount > 0).map((c) => ({ ...c, color: originColor(c) }));
  const flowList = (items, dir) => items.length ? items.map((x) => {
    const href = x.type === 'fund' ? `#/funds/${x.id}` : `#/accounts/${x.id}`;
    const dot = x.type === 'fund' ? swatch(x.color) : '';
    const kind = x.type === 'fund' ? (dir === 'in' ? 'allocated from fund' : 'allocated to fund') : x.type === 'in' ? 'source' : 'spending';
    return `<a class="list-item" href="${href}"><span class="row grow title">${dot}${esc(x.label)}</span><span class="small muted">${kind}</span><span class="num">${esc(money(x.amount))}</span></a>`;
  }).join('') : '<p class="muted">None yet.</p>';
  const where = d.by_account.length ? `${stackbar(d.by_account.filter((b) => b.amount > 0).map((b) => ({ label: b.account_name, amount: b.amount, color: 'var(--ink-2)' })))}
    <div class="list mt">${d.by_account.map((b) => `<div class="list-item"><a class="grow title" href="#/accounts/${b.account_id}">${esc(b.account_name)}${b.kind === 'liability' ? ' <span class="small muted">charged, not yet paid</span>' : ''}</a>
      ${b.kind === 'asset' ? cellBtn(b.account_id, f.id, b.amount, `${f.name} in ${b.account_name}`) : `<span class="num ${b.amount < 0 ? 'neg' : ''}">${esc(money(b.amount))}</span>`}</div>`).join('')}</div>
    <p class="small muted mt">Click an amount to change how much of that account belongs to ${esc(f.name)}.</p>`
    : `<p class="muted">This fund holds no money right now. <button class="btn btn-sm" data-action="new-txn" data-type="allocation" data-to-fund="${f.id}">Add money</button></p>`;
  const origin = comp.length ? `${stackbar(comp.map((c) => ({ label: c.label, amount: c.amount, color: c.color })))}
    ${legend(comp.map((c) => ({ label: c.label, color: c.color, value: `${money(c.amount)} · ${Math.round(c.share * 100)}%` })))}
    <p class="small muted mt">Traced through every allocation back to the original source, pro-rata.</p>`
    : d.composition.some((c) => c.kind === 'deficit') ? '<p class="callout warn">This fund is overdrawn — more was spent from it than it received.</p>' : '<p class="muted">Nothing to trace yet.</p>';
  return `${pageHead(`<span class="row">${swatch(f.color)}${esc(f.name)}${f.archived ? ' <span class="badge">archived</span>' : ''}</span>`, {
    crumb: '<a href="#/funds">Funds</a>',
    sub: esc(f.description),
    actions: `<button class="btn" data-action="new-txn" data-type="allocation" data-to-fund="${f.id}">Add money</button>
      <button class="btn" data-action="new-txn" data-type="allocation" data-from-fund="${f.id}">Move out</button>
      <button class="btn" data-action="new-txn" data-type="expense" data-fund="${f.id}">Record expense</button>
      <button class="btn" data-action="edit-fund" data-id="${f.id}">Edit</button>${fundMenu(f.id)}`,
  })}
  <div class="stack">
    <div class="stats">
      ${stat('Balance', esc(money(f.balance)), '', 'emph')}
      ${f.target ? stat('Target', esc(money(f.target)), `${meter(f.balance, f.target, f.balance >= f.target ? 'good' : '')}<div style="margin-top:6px">${esc(money(Math.max(0, f.target - f.balance)))} to go</div>`) : ''}
      ${stat('Total in', esc(money(totalIn)))}
      ${stat('Total out', esc(money(totalOut)))}
    </div>
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Where it is</h2><button class="btn btn-sm" data-action="adjust-allocation" data-fund="${f.id}">Adjust</button></div>${where}</div>
      <div class="card"><div class="card-head"><h2>Where it came from</h2><span class="muted">original sources</span></div>${origin}</div>
    </div>
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Money in</h2><span class="muted">${esc(money(totalIn))}</span></div><div class="list">${flowList(d.inflows, 'in')}</div></div>
      <div class="card"><div class="card-head"><h2>Money out</h2><span class="muted">${esc(money(totalOut))}</span></div><div class="list">${flowList(d.outflows, 'out')}</div></div>
    </div>
    ${d.obligations.length ? `<div class="card"><div class="card-head"><h2>Sinking fund for</h2></div><div class="list">${d.obligations.map((o) => `<div class="list-item"><a class="grow title" href="#/plan">${esc(o.name)}</a><span class="badge">${esc(KIND_LABELS[o.kind])}</span>${iconBtn('edit-obligation', { id: o.id }, `Edit ${o.name}`)}</div>`).join('')}</div></div>` : ''}
    <div class="card"><div class="card-head"><h2>History</h2><span class="muted">${d.history.length} transactions</span></div>
      ${txnTable(d.history, { delta: true, running: true, origins: true })}</div>
  </div>`;
}

// ---------------------------------------------------------------- accounts

const SUBTYPE = { bank: 'Bank', ewallet: 'E-wallet', cash: 'Cash', investment: 'Investment', credit_card: 'Credit card', loan: 'Loan' };

async function viewAccounts() {
  const [{ accounts }, bal] = await Promise.all([api('/accounts'), api('/balances')]);
  const showArchived = new URLSearchParams(location.hash.split('?')[1]).has('archived');
  const vis = accounts.filter((a) => showArchived || !a.archived);
  const holding = vis.filter((a) => HOLDING.includes(a.kind));
  const sources = vis.filter((a) => a.kind === 'income');
  const cats = vis.filter((a) => a.kind === 'expense');
  const holdingCards = holding.map((a) => {
    const segs = bal.cells.filter((c) => c.account_id === a.id && c.amount > 0)
      .map((c) => ({ fund_id: c.fund_id, fund_name: fund(c.fund_id)?.name, amount: c.amount }));
    return `<div class="card fund-card" data-href="#/accounts/${a.id}">
      <div class="row between"><a class="name" href="#/accounts/${a.id}">${esc(a.name)}</a>
        <span class="row"><span class="badge">${esc(SUBTYPE[a.subtype] || a.kind)}</span>${a.archived ? '<span class="badge">archived</span>' : ''}${iconBtn('edit-account', { id: a.id }, `Edit ${a.name}`)}${accountMenu(a.id)}</span></div>
      <div class="bal num ${a.kind === 'liability' && a.balance > 0 ? 'neg' : ''}">${esc(money(a.balance))}${a.kind === 'liability' ? ' <span class="small muted">owed</span>' : ''}</div>
      ${a.kind === 'asset' ? stackbar(fundSegments(segs)) : ''}
      ${a.kind === 'asset' && segs.length ? `<div class="chips">${segs.map((s) => `<button type="button" class="chip" data-action="adjust-allocation" data-account="${a.id}" data-fund="${s.fund_id}" title="Adjust ${esc(s.fund_name)} in ${esc(a.name)}">${esc(s.fund_name)} ${esc(money(s.amount))}</button>`).join('')}</div>` : ''}
      <div class="row wrap"><button class="btn btn-sm" data-action="reconcile-account" data-id="${a.id}">Reconcile</button>${a.kind === 'asset' ? `<button class="btn btn-sm" data-action="adjust-allocation" data-account="${a.id}">Adjust split</button>` : ''}</div>
    </div>`;
  }).join('');
  const simpleList = (list, label) => list.length ? list.map((a) => `<div class="list-item" data-href="#/accounts/${a.id}">
      <a class="grow title" href="#/accounts/${a.id}" title="${esc(a.name)}">${esc(a.name)}${a.archived ? ' <span class="badge">archived</span>' : ''}</a><span class="num" title="${label}">${esc(money(a.balance))}</span>
      ${iconBtn('edit-account', { id: a.id }, `Edit ${a.name}`)}${accountMenu(a.id)}</div>`).join('')
    : '<p class="muted">None yet.</p>';
  return `${pageHead('Accounts', {
    sub: 'Where money sits, where it comes from, and where it goes. Click a fund chip to adjust the split.',
    actions: `<a class="btn btn-ghost" href="#/accounts${showArchived ? '' : '?archived'}">${showArchived ? 'Hide' : 'Show'} archived</a>
      <button class="btn btn-primary" data-action="new-account">+ New account</button>`,
  })}
  <div class="stack">
    <h2>Where your money is</h2>
    <div class="grid grid-auto">${holdingCards || '<div class="card empty"><h3>No accounts yet</h3><p>Add your bank accounts, e-wallets and cash.</p></div>'}</div>
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Income sources</h2><button class="btn btn-sm" data-action="new-account" data-kind="income:">+ Source</button></div><div class="list">${simpleList(sources, 'received')}</div></div>
      <div class="card"><div class="card-head"><h2>Expense categories</h2><button class="btn btn-sm" data-action="new-account" data-kind="expense:">+ Category</button></div><div class="list">${simpleList(cats, 'spent')}</div></div>
    </div>
  </div>`;
}

async function viewAccountDetail(id) {
  const d = await api(`/accounts/${id}`);
  const a = d.account;
  const holding = HOLDING.includes(a.kind);
  const kindLabel = { asset: SUBTYPE[a.subtype] || 'Account', liability: SUBTYPE[a.subtype] || 'Liability', income: 'Income source', expense: 'Expense category', equity: 'Bookkeeping' }[a.kind];
  const byFund = d.by_fund.length ? `${stackbar(fundSegments(d.by_fund.filter((b) => b.amount > 0)))}
    <div class="list mt">${d.by_fund.map((b) => `<div class="list-item"><a class="row grow title" href="#/funds/${b.fund_id}">${swatch(b.color)}${esc(b.fund_name)}</a>
      ${a.kind === 'asset' ? cellBtn(a.id, b.fund_id, b.amount, `${b.fund_name} in ${a.name}`) : `<span class="num ${b.amount < 0 ? 'neg' : ''}">${esc(money(b.amount))}</span>`}</div>`).join('')}</div>
    ${a.kind === 'asset' ? '<p class="small muted mt">Click an amount to move money between funds inside this account.</p>' : ''}` : '<p class="muted">Nothing here yet.</p>';
  const balLabel = { asset: 'Balance', liability: 'Owed', income: 'Total received', expense: 'Total spent', equity: 'Total' }[a.kind];
  return `${pageHead(`${esc(a.name)}${a.archived ? ' <span class="badge">archived</span>' : ''}`, {
    crumb: '<a href="#/accounts">Accounts</a>',
    sub: `${esc(kindLabel)}${a.notes ? ` · ${esc(a.notes)}` : ''}`,
    actions: `${holding ? `<button class="btn" data-action="reconcile-account" data-id="${a.id}">Reconcile</button>
      <button class="btn" data-action="new-txn" data-type="transfer" data-from-account="${a.id}">Transfer</button>` : ''}
      ${a.kind === 'income' ? `<button class="btn" data-action="new-txn" data-type="income" data-source="${a.id}">+ Income</button>` : ''}
      ${a.kind === 'expense' ? `<button class="btn" data-action="new-txn" data-type="expense" data-category="${a.id}">+ Expense</button>` : ''}
      <button class="btn" data-action="edit-account" data-id="${a.id}">Edit</button>${accountMenu(a.id)}`,
  })}
  <div class="stack">
    <div class="grid grid-2">
      <div class="card"><div class="stat-label">${balLabel}</div><div class="hero-figure">${esc(money(a.balance))}</div>
        ${holding ? '<p class="small muted mt">Does this match your bank or e-wallet app? If not, use Reconcile.</p>' : ''}</div>
      <div class="card"><div class="card-head"><h2>${holding ? 'Split by fund' : 'By fund'}</h2>${a.kind === 'asset' ? `<button class="btn btn-sm" data-action="adjust-allocation" data-account="${a.id}">Adjust split</button>` : '<span class="muted">what this money is for</span>'}</div>${byFund}</div>
    </div>
    <div class="card"><div class="card-head"><h2>History</h2><span class="muted">${d.history.length} transactions</span></div>
      ${txnTable(d.history, { delta: true, running: true })}</div>
  </div>`;
}

// ------------------------------------------------------------------ ledger

const ledgerState = { q: '', type: '', fund_id: '', account_id: '', start: '', end: '', include_voided: false, limit: 100 };

async function viewLedger() {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(ledgerState)) if (v) params.set(k, v === true ? '1' : v);
  const { items, total } = await api(`/transactions?${params}`);
  const typeOpts = Object.entries(TYPE_LABELS).map(([k, l]) => `<option value="${k}" ${ledgerState.type === k ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const fundOpts = S.funds.map((f) => `<option value="${f.id}" ${String(ledgerState.fund_id) === String(f.id) ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
  const acctOpts = S.accounts.filter((a) => a.kind !== 'equity').map((a) => `<option value="${a.id}" ${String(ledgerState.account_id) === String(a.id) ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
  return `${pageHead('Ledger', {
    sub: 'Every transaction, double-entry. Edit, duplicate, void or restore any row — every change is kept in the audit log.',
    actions: '<a class="btn" href="/api/export/transactions.csv">Export CSV</a><button class="btn btn-primary" data-action="new-txn">+ New transaction</button>',
  })}
  <div class="card">
    <form class="filters" data-ledger-filters>
      <input type="search" name="q" placeholder="Search description or reference" value="${esc(ledgerState.q)}" aria-label="Search">
      <select name="type" aria-label="Type"><option value="">All types</option>${typeOpts}</select>
      <select name="fund_id" aria-label="Fund"><option value="">All funds</option>${fundOpts}</select>
      <select name="account_id" aria-label="Account"><option value="">All accounts</option>${acctOpts}</select>
      <input type="date" name="start" value="${esc(ledgerState.start)}" aria-label="From date">
      <input type="date" name="end" value="${esc(ledgerState.end)}" aria-label="To date">
      <label class="check"><input type="checkbox" name="include_voided" ${ledgerState.include_voided ? 'checked' : ''}> Show voided</label>
    </form>
    <p class="small muted" style="margin-bottom:8px">${total} transaction${total === 1 ? '' : 's'}${total > items.length ? ` · showing ${items.length}` : ''}</p>
    ${txnTable(items)}
    ${total > items.length ? '<div class="row mt" style="justify-content:center"><button class="btn" data-action="ledger-more">Load more</button></div>' : ''}
  </div>`;
}

// ------------------------------------------------------------- obligations

async function viewPlan() {
  const p = await api('/plan');
  lastPlan = p;
  const t = p.totals;
  const cards = p.obligations.map((o) => {
    const need = o.amount * (o.due_now_count + (o.upcoming_due ? 1 : 0));
    const progress = o.total_payments ? `<div class="meter-row">${meter(o.paid_count, o.total_payments, o.completed ? 'good' : '')}
        <span>${o.paid_count} of ${o.total_payments} paid · ${esc(money(o.remaining_balance))} remaining${o.end_date ? ` · ends ${esc(fmtDate(o.end_date))}` : ''}</span></div>` : '';
    const reserve = o.fund_id && !o.completed ? `<div class="meter-row">${meter(o.reserved, need || o.amount, o.reserved >= need ? 'good' : o.state === 'overdue' ? 'critical' : '')}
        <span>${esc(money(o.reserved))} saved in <a href="#/funds/${o.fund_id}">${esc(o.fund_name)}</a>${o.shared_fund ? ' (shared)' : ''} · ${esc(money(need))} needed${o.payments_covered > 1 ? ` · covers ${o.payments_covered} payments` : ''}</span></div>` : '';
    return `<div class="card ob-card">
      <div class="ob-top">
        <div><div class="row wrap"><h2>${esc(o.name)}</h2><span class="badge">${esc(KIND_LABELS[o.kind])}</span></div>
          <div class="small muted">${esc(FREQ_LABELS[o.frequency])}${o.total_payments ? '' : ' · ongoing'}</div></div>
        <div class="right"><div class="row" style="justify-content:flex-end"><div class="ob-amount num">${esc(money(o.amount))}<span class="small muted">${FREQ_SHORT[o.frequency]}</span></div>${obligationMenu(o)}</div>${statusPill(o.state)}</div>
      </div>
      <dl class="kv">
        <div><dt>Next due</dt><dd>${o.next_due ? `${esc(fmtDate(o.next_due))} <span class="muted small">${esc(relDays(o.next_due))}</span>` : '—'}</dd></div>
        <div><dt>Set aside this month</dt><dd class="num">${o.set_aside_this_month
          ? `${esc(money(o.set_aside_this_month))}${o.months_to_save > 1 ? ` <span class="muted small">· ${o.months_to_save} mo to go</span>` : ''}`
          : o.contributed_this_month ? `<span class="pos">✓ Done</span> <span class="muted small">${esc(money(o.contributed_this_month))} saved</span>` : esc(money(0))}</dd></div>
        <div><dt>Monthly equivalent</dt><dd class="num">${esc(money(o.monthly_equivalent))}</dd></div>
        <div><dt>${o.overdue_count ? 'Overdue' : 'Paid so far'}</dt><dd class="num ${o.overdue_count ? 'neg' : ''}">${o.overdue_count ? `${o.overdue_count} installment${o.overdue_count > 1 ? 's' : ''} · ${esc(money(o.overdue_count * o.amount))}` : esc(money(o.paid_in_ledger))}</dd></div>
      </dl>
      ${progress}${reserve}
      <div class="row wrap">
        ${o.completed ? '' : `<button class="btn btn-primary btn-sm" data-action="pay-obligation" data-id="${o.id}" data-name="${esc(o.name)}">Record payment</button>`}
        <button class="btn btn-sm" data-action="obligation-schedule" data-id="${o.id}">Schedule</button>
        ${o.fund_id && o.set_aside_this_month ? `<button class="btn btn-sm" data-action="new-txn" data-type="allocation" data-to-fund="${o.fund_id}" data-amount="${o.set_aside_this_month}">Set aside ${esc(money(o.set_aside_this_month))}</button>` : ''}
        <button class="btn btn-ghost btn-sm" data-action="edit-obligation" data-id="${o.id}">Edit</button>
      </div>
    </div>`;
  }).join('');
  const upcoming = p.upcoming.length ? `<div class="table-wrap"><table><thead><tr><th>Due</th><th>Obligation</th><th>Installment</th><th class="num">Amount</th><th class="num">Remaining after</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${p.upcoming.slice(0, 24).map((u) => `<tr><td class="nowrap ${u.overdue ? 'neg' : ''}">${esc(fmtDate(u.date))}${u.overdue ? ' · overdue' : ''}</td><td>${esc(u.name)}</td>
      <td>${u.of ? `#${u.number} of ${u.of}` : `#${u.number}`}</td><td class="num">${esc(money(u.amount))}</td><td class="num">${u.remaining_after != null ? esc(money(u.remaining_after)) : '<span class="muted">ongoing</span>'}</td>
      <td class="actions-cell">${iconBtn('pay-obligation', { id: u.obligation_id, name: u.name }, `Record payment for ${u.name}`, '₱')}${iconBtn('edit-obligation', { id: u.obligation_id }, `Edit ${u.name}`)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">Nothing scheduled.</p>';
  const archived = p.archived.length ? `<div class="card"><div class="card-head"><h2>Archived obligations</h2><span class="muted">hidden from the plan</span></div>
    <div class="list">${p.archived.map((o) => `<div class="list-item"><span class="grow title">${esc(o.name)} <span class="badge">${esc(KIND_LABELS[o.kind])}</span></span>
      <span class="num small muted">${esc(money(o.amount))}${FREQ_SHORT[o.frequency]}</span>
      <button class="btn btn-sm" data-action="archive-obligation" data-id="${o.id}" data-archived="1">Restore</button>${obligationMenu({ ...o, archived: 1 })}</div>`).join('')}</div></div>` : '';
  return `${pageHead('Obligations', {
    sub: 'Amortizations, insurance and other recurring payments — what to save, and what is left to spend.',
    actions: `<button class="btn" data-action="fund-set-asides" ${t.set_aside_this_month + t.shortfall_now > 0 ? '' : 'disabled'}>Fund this month’s set-asides</button>
      <button class="btn btn-primary" data-action="new-obligation">+ New obligation</button>`,
  })}
  <div class="stack">
    <div class="stats">
      ${stat('Free to spend', esc(money(p.free_to_spend)), `${esc(money(p.unallocated))} unallocated − set-asides${t.shortfall_now ? ' − shortfall' : ''}`, 'emph')}
      ${stat('Set aside this month', esc(money(t.set_aside_this_month)), 'into sinking funds')}
      ${stat('Due now, not covered', `<span class="${t.shortfall_now ? 'neg' : ''}">${esc(money(t.shortfall_now))}</span>`, t.shortfall_now ? 'overdue or due today' : 'all covered')}
      ${stat('Due in next 30 days', esc(money(t.due_next_30_days)))}
      ${stat('Monthly commitments', esc(money(t.monthly_commitment)), `${esc(money(t.annual_cost))} per year`)}
      ${stat('Remaining balances', esc(money(t.remaining_balance)), 'on fixed-term contracts')}
    </div>
    ${p.free_to_spend < 0 ? `<p class="callout warn">${statusPill('overdue')} Your unallocated money does not cover this month’s set-asides. Consider moving money from other funds, or postpone discretionary spending.</p>` : ''}
    ${p.obligations.length ? `<div class="grid grid-2">${cards}</div>` : `<div class="card empty"><h3>No obligations yet</h3><p>Add car or housing amortizations, insurance premiums, tuition, subscriptions — anything you pay on a schedule.</p><p class="mt"><button class="btn btn-primary" data-action="new-obligation">+ Add obligation</button></p></div>`}
    <div class="card"><div class="card-head"><h2>Payment calendar</h2><span class="muted">next 12 months</span></div>${upcoming}</div>
    ${archived}
    <div class="card small ink-2"><h3>How the numbers work</h3>
      <p class="mt">Each obligation saves into its <b>sinking fund</b>. <b>Set aside this month</b> = (next payment − what was saved before this month) ÷ months left until it is due — so a ₱24,000 yearly premium due in 5 months needs ₱4,800 a month. Money you put into the fund during the month counts toward that, and opening balances count as already saved. <b>Remaining balance</b> = unpaid installments × amount, counted from the payments you record. <b>Free to spend</b> is your Unallocated money after this month’s set-asides and anything overdue. Paid before you started tracking? Edit the obligation and set “Already paid before tracking”.</p></div>
  </div>`;
}

async function showSchedule(id) {
  const d = await api(`/obligations/${id}`);
  const o = d.obligation;
  const dlg = document.getElementById('modal');
  const rows = d.schedule.map((s) => `<tr><td>#${s.number}${s.of ? ` / ${s.of}` : ''}</td><td class="${s.overdue ? 'neg' : ''}">${esc(fmtDate(s.date))}</td><td class="num">${esc(money(s.amount))}</td><td class="num">${s.remaining_after != null ? esc(money(s.remaining_after)) : '—'}</td></tr>`).join('');
  const pays = d.payments.map((t) => `<tr class="clickable" data-action="open-txn" data-id="${t.id}"><td>${esc(fmtDate(t.date))}</td><td>${esc(t.description)}</td><td class="num">${esc(money(t.amount))}</td>
    <td class="actions-cell">${iconBtn('edit-txn', { id: t.id }, 'Edit payment')}</td></tr>`).join('');
  dlg.innerHTML = `<div class="modal-inner"><div class="modal-head"><h2>${esc(o.name)}</h2><button class="btn btn-ghost btn-sm" data-close-modal aria-label="Close">✕</button></div>
    <div class="modal-body">
      <p class="callout">${o.total_payments ? `${o.paid_count} of ${o.total_payments} installments paid (${o.prior_payments} before tracking). Remaining: <b>${esc(money(o.remaining_balance))}</b> over ${o.remaining_payments} payments.` : `Ongoing ${esc(FREQ_LABELS[o.frequency].toLowerCase())} payment of ${esc(money(o.amount))} — ${esc(money(o.annual_cost))} per year.`}</p>
      <h3>Remaining schedule</h3>
      <div class="table-wrap"><table><thead><tr><th>Installment</th><th>Due</th><th class="num">Amount</th><th class="num">Balance after</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">Fully paid.</td></tr>'}</tbody></table></div>
      <h3>Payments recorded in FundFlow</h3>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th class="num">Amount</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${pays || '<tr><td colspan="4" class="muted">None yet.</td></tr>'}</tbody></table></div>
    </div>
    <div class="modal-foot"><button class="btn" data-action="edit-obligation" data-id="${o.id}">Edit obligation</button>
      ${o.completed ? '' : `<button class="btn btn-primary" data-action="pay-obligation" data-id="${o.id}" data-name="${esc(o.name)}">Record payment</button>`}</div></div>`;
  dlg.style.width = 'min(760px, calc(100vw - 32px))';
  dlg.querySelector('[data-close-modal]').addEventListener('click', () => dlg.close());
  if (!dlg.open) dlg.showModal();
}

// -------------------------------------------------------------------- flow

const flowState = { range: '3m' };
function rangeDates(range) {
  const t = new Date(today() + 'T00:00:00');
  const iso = (d) => d.toISOString().slice(0, 10);
  const startOfMonth = (back) => iso(new Date(Date.UTC(t.getFullYear(), t.getMonth() - back, 1)));
  switch (range) {
    case 'month': return { start: startOfMonth(0), end: today() };
    case '3m': return { start: startOfMonth(2), end: today() };
    case 'year': return { start: `${t.getFullYear()}-01-01`, end: today() };
    default: return { start: '', end: '' };
  }
}

async function viewFlow() {
  const { start, end } = rangeDates(flowState.range);
  const data = await api(`/flows?start=${start}&end=${end}`);
  const seg = [['month', 'This month'], ['3m', 'Last 3 months'], ['year', 'This year'], ['all', 'All time']]
    .map(([k, l]) => `<button type="button" data-action="flow-range" data-range="${k}" aria-pressed="${flowState.range === k}">${l}</button>`).join('');
  const comp = data.composition.map((c) => {
    const items = c.origins.filter((o) => o.amount > 0).map((o) => ({ ...o, color: originColor(o) }));
    if (!items.length) return '';
    return `<div class="list-item stacked">
      <div class="row between"><a class="title" href="#/funds/${c.fund_id}">${esc(c.label)}</a><span class="row"><span class="num">${esc(money(items.reduce((s, i) => s + i.amount, 0)))}</span>${fundMenu(c.fund_id)}</span></div>
      ${stackbar(items)}</div>`;
  }).join('');
  const allOrigins = new Map();
  for (const c of [...data.composition.flatMap((x) => x.origins), ...data.categories.flatMap((x) => x.origins)]) {
    if (c.amount > 0 && !allOrigins.has(c.key)) allOrigins.set(c.key, { label: c.label, color: originColor(c) });
  }
  const cats = data.categories.map((c) => `<div class="list-item stacked">
      <div class="row between"><a class="title" href="#/accounts/${c.account_id}">${esc(c.label)}</a><span class="row"><span class="num">${esc(money(c.total))}</span>${accountMenu(c.account_id)}</span></div>
      ${stackbar(c.origins.filter((o) => o.amount > 0).map((o) => ({ ...o, color: originColor(o) })))}</div>`).join('');
  return {
    html: `${pageHead('Flow', { sub: 'Follow every peso: from its source, through your funds, to where it was spent. Click any node to open it.' })}
    <div class="stack">
      <div class="filters"><div class="seg" role="group" aria-label="Date range">${seg}</div>
        <span class="small muted">${start ? `${esc(fmtDate(start))} – ${esc(fmtDate(end))}` : 'All time'}</span></div>
      <div class="card"><div class="card-head"><h2>Money flow</h2><span class="muted">hover a band for details</span></div>
        <div class="sankey-wrap" data-sankey></div></div>
      <div class="grid grid-2">
        <div class="card"><div class="card-head"><h2>What each fund is made of</h2><span class="muted">current balance by original source</span></div>
          ${allOrigins.size ? legend([...allOrigins.values()]) : ''}<div class="list mt">${comp || '<p class="muted">No balances yet.</p>'}</div></div>
        <div class="card"><div class="card-head"><h2>Spending, traced to its source</h2><span class="muted">in this period</span></div>
          ${allOrigins.size && cats ? legend([...allOrigins.values()]) : ''}<div class="list mt">${cats || '<p class="muted">No spending in this period.</p>'}</div></div>
      </div>
    </div>`,
    after: () => {
      const el = main.querySelector('[data-sankey]');
      const draw = () => {
        if (!renderSankey(el, data)) el.innerHTML = '<p class="empty">No money moved in this period.</p>';
      };
      draw();
      main._resize = draw;
    },
  };
}

// ---------------------------------------------------------------- settings

async function viewSettings() {
  const [integrity, audit, { funds }, { accounts }, plan] = await Promise.all([
    api('/integrity'), api('/audit?limit=50'), api('/funds'), api('/accounts'), api('/plan'),
  ]);
  lastPlan = plan;
  const s = S.settings;
  const theme = localStorageGet('ff-theme') || 'auto';
  const auditRows = audit.items.map((a) => `<tr><td class="nowrap small">${esc(a.at)}</td><td><span class="badge">${esc(a.action)}</span></td><td>${esc(a.entity)}${a.entity_id ? ` #${a.entity_id}` : ''}</td>
    <td class="small muted" style="max-width:420px;overflow-wrap:anywhere">${esc(a.detail.length > 160 ? a.detail.slice(0, 160) + '…' : a.detail)}</td></tr>`).join('');
  const status = (r) => [r.is_system ? '<span class="badge">system</span>' : '', r.archived ? '<span class="badge">archived</span>' : ''].join(' ');
  const table = (heads, rows) => `<div class="table-wrap"><table><thead><tr>${heads.map((h) => `<th class="${h.endsWith('#') ? 'num' : ''}">${esc(h.replace('#', ''))}</th>`).join('')}<th><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows.join('') || `<tr><td colspan="${heads.length + 1}" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
  const fundRows = funds.map((f) => `<tr><td><a class="row" href="#/funds/${f.id}">${swatch(f.color)}${esc(f.name)}</a></td><td>${status(f)}</td><td class="num">${f.txn_count}</td><td class="num">${esc(money(f.balance))}</td>
    <td class="actions-cell">${iconBtn('edit-fund', { id: f.id }, `Edit ${f.name}`)}${fundMenu(f.id)}</td></tr>`);
  const acctRows = (kinds, label) => accounts.filter((a) => kinds.includes(a.kind)).map((a) => `<tr><td><a href="#/accounts/${a.id}">${esc(a.name)}</a></td><td>${esc(label(a))} ${status(a)}</td><td class="num">${a.txn_count}</td><td class="num">${esc(money(a.balance))}</td>
    <td class="actions-cell">${iconBtn('edit-account', { id: a.id }, `Edit ${a.name}`)}${accountMenu(a.id)}</td></tr>`);
  const obRows = [...plan.obligations, ...plan.archived.map((o) => ({ ...o, archived: 1 }))].map((o) => `<tr><td>${esc(o.name)}</td><td>${esc(KIND_LABELS[o.kind])} ${o.archived ? '<span class="badge">archived</span>' : ''}</td><td class="num">${o.txn_count}</td><td class="num">${esc(money(o.amount))}${FREQ_SHORT[o.frequency]}</td>
    <td class="actions-cell">${iconBtn('edit-obligation', { id: o.id }, `Edit ${o.name}`)}${obligationMenu(o)}</td></tr>`);
  const themeSeg = ['auto', 'light', 'dark'].map((m) => `<button type="button" data-action="set-theme" data-mode="${m}" aria-pressed="${theme === m}">${m[0].toUpperCase() + m.slice(1)}</button>`).join('');
  return `${pageHead('Settings', { sub: `FundFlow v${esc(S.meta.version)}` })}
  <div class="stack">
    <div class="card"><div class="card-head"><h2>Manage everything</h2><span class="muted">edit, archive, restore or delete anything — deleting something with history lets you move its transactions or delete them too</span></div>
      <div class="tabs" role="tablist" data-manage-tabs>
        ${['Funds', 'Accounts', 'Sources', 'Categories', 'Obligations'].map((t, i) => `<button type="button" data-manage="${t}" aria-pressed="${i === manageTab.index}">${t}</button>`).join('')}
      </div>
      <div class="mt" data-manage-panel="Funds" ${manageTab.index === 0 ? '' : 'hidden'}>${table(['Fund', 'Status', 'Transactions#', 'Balance#'], fundRows)}<div class="mt"><button class="btn btn-sm" data-action="new-fund">+ New fund</button></div></div>
      <div class="mt" data-manage-panel="Accounts" ${manageTab.index === 1 ? '' : 'hidden'}>${table(['Account', 'Type', 'Transactions#', 'Balance#'], acctRows(HOLDING, (a) => SUBTYPE[a.subtype] || a.kind))}<div class="mt"><button class="btn btn-sm" data-action="new-account">+ New account</button></div></div>
      <div class="mt" data-manage-panel="Sources" ${manageTab.index === 2 ? '' : 'hidden'}>${table(['Source', 'Type', 'Transactions#', 'Received#'], acctRows(['income'], () => 'Income'))}<div class="mt"><button class="btn btn-sm" data-action="new-account" data-kind="income:">+ New source</button></div></div>
      <div class="mt" data-manage-panel="Categories" ${manageTab.index === 3 ? '' : 'hidden'}>${table(['Category', 'Type', 'Transactions#', 'Spent#'], acctRows(['expense'], () => 'Expense'))}<div class="mt"><button class="btn btn-sm" data-action="new-account" data-kind="expense:">+ New category</button></div></div>
      <div class="mt" data-manage-panel="Obligations" ${manageTab.index === 4 ? '' : 'hidden'}>${table(['Obligation', 'Type', 'Payments#', 'Amount#'], obRows)}<div class="mt"><button class="btn btn-sm" data-action="new-obligation">+ New obligation</button></div></div>
    </div>
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Currency & appearance</h2></div>
        <form class="form-grid" data-settings-form>
          <label class="field">Symbol<input name="currency_symbol" value="${esc(s.currency_symbol)}"></label>
          <label class="field">Code<input name="currency_code" value="${esc(s.currency_code)}"></label>
          <label class="field span-2">Number format locale<input name="locale" value="${esc(s.locale)}" placeholder="en-PH"></label>
          <div class="span-2"><button class="btn btn-primary" type="submit">Save</button></div>
        </form>
        <div class="field mt"><span>Theme</span><div class="seg" role="group" aria-label="Theme">${themeSeg}</div></div></div>
      <div class="card"><div class="card-head"><h2>Ledger integrity</h2>${statusPill(integrity.ok ? 'funded' : 'overdue').replace('Funded', 'Verified').replace('Overdue', 'Problems found')}</div>
        <p class="small ink-2">Every transaction balances to zero and fund balances add up to the money in your accounts. Transactions are never deleted, only voided — and voids can be undone.</p>
        <dl class="kv mt">
          <div><dt>Transactions</dt><dd>${integrity.transactions} <span class="muted small">(${integrity.voided} voided)</span></dd></div>
          <div><dt>Postings</dt><dd>${integrity.postings}</dd></div>
          <div><dt>Sum of all funds</dt><dd class="num">${esc(money(integrity.fund_total))}</dd></div>
          <div><dt>Net worth</dt><dd class="num">${esc(money(integrity.net_worth))}</dd></div>
        </dl>
        ${integrity.problems.map((p) => `<p class="callout warn mt">${esc(p)}</p>`).join('')}
        ${integrity.overdrawn.length ? `<p class="callout warn mt">Overdrawn: ${integrity.overdrawn.map((o) => `<button type="button" class="chip" data-action="adjust-allocation" data-account="${o.account_id}" data-fund="${o.fund_id}">${esc(acct(o.account_id)?.name)} · ${esc(fund(o.fund_id)?.name)} (${esc(money(o.amount))})</button>`).join(' ')}</p>` : ''}
      </div>
    </div>
    <div class="card"><div class="card-head"><h2>Your data</h2></div>
      <p class="small ink-2">Everything is stored in one SQLite file on this computer (<code>data/fundflow.db</code>). It is never uploaded and is excluded from git. Keep backups.</p>
      <div class="row wrap mt"><a class="btn" href="/api/export/transactions.csv">Export transactions (CSV)</a><a class="btn" href="/api/export/backup.json">Download full backup (JSON)</a></div></div>
    <div class="card"><div class="card-head"><h2>Audit log</h2><span class="muted">latest ${audit.items.length} of ${audit.total} — append-only</span></div>
      <div class="table-wrap"><table><thead><tr><th>When (UTC)</th><th>Action</th><th>Record</th><th>Detail</th></tr></thead><tbody>${auditRows}</tbody></table></div></div>
  </div>`;
}
const manageTab = { index: 0 };

// ------------------------------------------------------------------ router

const ROUTES = {
  '': viewDashboard,
  funds: (id) => (id ? viewFundDetail(id) : viewFunds()),
  accounts: (id) => (id ? viewAccountDetail(id) : viewAccounts()),
  ledger: viewLedger,
  plan: viewPlan,
  flow: viewFlow,
  settings: viewSettings,
};

let routing = 0;
async function render({ keepScroll = false } = {}) {
  const hash = location.hash.replace(/^#\/?/, '').split('?')[0];
  const [name, id] = hash.split('/');
  const view = ROUTES[name] ?? ROUTES[''];
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === (ROUTES[name] ? name : '')));
  const ticket = ++routing;
  const y = scrollY;
  main._resize = null;
  try {
    const out = await view(id);
    if (ticket !== routing) return;
    main.innerHTML = typeof out === 'string' ? out : out.html;
    out.after?.();
    document.title = `${document.querySelector('.page-head h1')?.textContent.trim() || 'FundFlow'} · FundFlow`;
    if (keepScroll) scrollTo(0, y);
  } catch (e) {
    if (ticket !== routing) return;
    main.innerHTML = `<div class="card empty"><h3>Something went wrong</h3><p>${esc(e.message)}</p><p class="mt"><a href="#/">Back to dashboard</a></p></div>`;
  }
}

async function refresh() {
  await loadRefs();
  await render({ keepScroll: true });
}

// ----------------------------------------------------------------- actions

async function setArchived(path, archived, noun) {
  await api(path, { method: 'PUT', body: { archived } });
  toast(archived ? `${noun} archived` : `${noun} restored`);
  await refresh();
}

const ACTIONS = {
  go: (el) => { location.hash = el.dataset.href; },
  'new-txn': (el) => {
    const d = el.dataset;
    const preset = {};
    if (d.fund) preset.fund_id = Number(d.fund);
    if (d.fromFund) preset.from_fund_id = Number(d.fromFund);
    if (d.toFund) preset.to_fund_id = Number(d.toFund);
    if (d.fromAccount) preset.from_account_id = Number(d.fromAccount);
    if (d.account) preset.account_id = Number(d.account);
    if (d.source) preset.source_id = Number(d.source);
    if (d.category) preset.category_id = Number(d.category);
    if (d.amount) preset.amount = Number(d.amount);
    return openTxnModal({ type: d.type || 'expense', preset });
  },
  'open-txn': (el) => openTxnDetail(el.dataset.id),
  'edit-txn': (el) => editTxn(el.dataset.id),
  'duplicate-txn': (el) => duplicateTxn(el.dataset.id),
  'void-txn': (el) => voidTxn(el.dataset.id),
  'restore-txn': (el) => restoreTxn(el.dataset.id),
  'new-fund': () => openFundModal(),
  'edit-fund': (el) => openFundModal(fund(el.dataset.id)),
  'archive-fund': (el) => setArchived(`/funds/${el.dataset.id}`, el.dataset.archived !== '1', 'Fund'),
  'delete-fund': (el) => deleteRecord('fund', el.dataset.id),
  'new-account': (el) => openAccountModal(null, el.dataset.kind || 'asset:bank'),
  'edit-account': (el) => openAccountModal(acct(el.dataset.id)),
  'archive-account': (el) => setArchived(`/accounts/${el.dataset.id}`, el.dataset.archived !== '1', 'Account'),
  'delete-account': (el) => deleteRecord('account', el.dataset.id),
  'reconcile-account': (el) => openReconcile(el.dataset.id),
  'adjust-allocation': (el) => openAdjustAllocation({
    accountId: el.dataset.account ? Number(el.dataset.account) : null,
    fundId: el.dataset.fund ? Number(el.dataset.fund) : null,
  }),
  'new-obligation': () => openObligationModal(),
  'edit-obligation': async (el) => {
    const { obligation } = await api(`/obligations/${el.dataset.id}`);
    return openObligationModal(obligation);
  },
  'archive-obligation': (el) => setArchived(`/obligations/${el.dataset.id}`, el.dataset.archived !== '1', 'Obligation'),
  'delete-obligation': (el) => deleteRecord('obligation', el.dataset.id),
  'pay-obligation': (el) => openTxnModal({
    type: 'payment', preset: { obligation_id: Number(el.dataset.id), description: el.dataset.name || '' },
  }),
  'obligation-schedule': (el) => showSchedule(el.dataset.id),
  'fund-set-asides': async () => openFundSetAsides(lastPlan || await api('/plan')),
  'ledger-more': () => { ledgerState.limit += 100; return render({ keepScroll: true }); },
  'flow-range': (el) => { flowState.range = el.dataset.range; return render({ keepScroll: true }); },
  'toggle-theme': () => {
    const order = ['auto', 'light', 'dark'];
    const cur = localStorageGet('ff-theme') || 'auto';
    applyTheme(order[(order.indexOf(cur) + 1) % order.length]);
    return render({ keepScroll: true });
  },
  'set-theme': (el) => { applyTheme(el.dataset.mode); return render({ keepScroll: true }); },
};

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

function applyTheme(mode) {
  if (mode === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  localStorageSet('ff-theme', mode);
  const btn = document.querySelector('.theme-toggle');
  if (btn) btn.textContent = `Theme: ${mode}`;
}

// ------------------------------------------------------------ interactions

const closeMenus = (except = null) =>
  document.querySelectorAll('details.menu[open]').forEach((d) => { if (d !== except) d.open = false; });

document.addEventListener('click', async (e) => {
  const inMenu = e.target.closest('details.menu');
  closeMenus(inMenu);
  const el = e.target.closest('[data-action]');
  // Clicking a menu's ⋯ inside a clickable row toggles the menu, not the row.
  if (inMenu && (!el || !inMenu.contains(el))) return;
  if (el) {
    if (el.disabled) return;
    const fn = ACTIONS[el.dataset.action];
    if (!fn) return;
    e.preventDefault();
    closeMenus();
    try {
      await fn(el, e);
    } catch (ex) {
      toast(ex.message, { error: true });
    }
    return;
  }
  const tab = e.target.closest('[data-manage]');
  if (tab) {
    const tabs = [...main.querySelectorAll('[data-manage]')];
    manageTab.index = tabs.indexOf(tab);
    tabs.forEach((t) => t.setAttribute('aria-pressed', String(t === tab)));
    main.querySelectorAll('[data-manage-panel]').forEach((p) => { p.hidden = p.dataset.managePanel !== tab.dataset.manage; });
    return;
  }
  if (e.target.closest('a, button, input, select, textarea, label, summary, dialog')) return;
  const nav = e.target.closest('[data-href]');
  if (nav) location.hash = nav.dataset.href;
});

// Menus pop out with fixed positioning so tables and cards never clip them.
document.addEventListener('toggle', (e) => {
  const d = e.target;
  if (!(d instanceof HTMLDetailsElement) || !d.classList.contains('menu') || !d.open) return;
  closeMenus(d);
  const pop = d.querySelector('.menu-pop');
  const r = d.querySelector('summary').getBoundingClientRect();
  const pr = pop.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.right - pr.width, innerWidth - pr.width - 8));
  const top = r.bottom + 4 + pr.height > innerHeight - 8 ? Math.max(8, r.top - pr.height - 4) : r.bottom + 4;
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
  pop.querySelector('.menu-item')?.focus({ preventScroll: true });
}, true);
addEventListener('scroll', () => closeMenus(), true);
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const open = document.querySelector('details.menu[open]');
  if (open) { open.open = false; open.querySelector('summary').focus(); }
});

main.addEventListener('input', (e) => {
  const form = e.target.closest('[data-ledger-filters]');
  if (!form) return;
  const fd = new FormData(form);
  Object.assign(ledgerState, {
    q: fd.get('q') || '', type: fd.get('type') || '', fund_id: fd.get('fund_id') || '', account_id: fd.get('account_id') || '',
    start: fd.get('start') || '', end: fd.get('end') || '', include_voided: fd.has('include_voided'), limit: 100,
  });
  clearTimeout(main._debounce);
  main._debounce = setTimeout(async () => {
    const focused = document.activeElement?.name;
    await render({ keepScroll: true });
    if (focused) {
      const again = main.querySelector(`[data-ledger-filters] [name="${focused}"]`);
      again?.focus();
      if (again?.type === 'search') again.setSelectionRange(again.value.length, again.value.length);
    }
  }, 250);
});

main.addEventListener('submit', async (e) => {
  const form = e.target.closest('[data-settings-form]');
  if (!form) return;
  e.preventDefault();
  try {
    await api('/settings', { method: 'PUT', body: Object.fromEntries(new FormData(form)) });
    toast('Settings saved');
    await refresh();
  } catch (ex) {
    toast(ex.message, { error: true });
  }
});

let resizeTimer;
addEventListener('resize', () => {
  closeMenus();
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => main._resize?.(), 150);
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => render({ keepScroll: true }));

onSaved(refresh);
addEventListener('hashchange', () => { render(); scrollTo(0, 0); });

(async function boot() {
  applyTheme(localStorageGet('ff-theme') || 'auto');
  initTooltips();
  try {
    await loadRefs();
  } catch (e) {
    main.innerHTML = `<div class="card empty"><h3>Cannot reach the FundFlow server</h3><p>${esc(e.message)}</p></div>`;
    return;
  }
  await render();
})();
