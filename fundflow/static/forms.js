// Modal forms: transactions, funds, accounts, obligations.
import {
  FREQ_LABELS, KIND_LABELS, PALETTE, S, acct, api, col, esc, fmtDate, fund, loadRefs, money, parseAmount,
  toInput, toast, today, typeBadge,
} from './util.js';

const dlg = document.getElementById('modal');
let afterSave = () => {};
export const onSaved = (fn) => { afterSave = fn; };

function modalShell({ title, body, submit = 'Save', danger = '', wide = false }) {
  return `<form method="dialog" novalidate ${wide ? 'data-wide' : ''}>
    <div class="modal-head"><h2>${esc(title)}</h2><button type="button" class="btn btn-ghost btn-sm" data-close aria-label="Close">✕</button></div>
    <div class="modal-body">${body}</div>
    <div class="modal-foot"><p class="form-error" role="alert"></p>${danger}
      <button type="button" class="btn" data-close>${submit ? 'Cancel' : 'Close'}</button>
      ${submit ? `<button type="submit" class="btn btn-primary">${esc(submit)}</button>` : ''}</div>
  </form>`;
}

function open(html, { onSubmit, onInput, onClick } = {}) {
  dlg.innerHTML = html;
  const form = dlg.querySelector('form');
  dlg.style.width = form.hasAttribute('data-wide') ? 'min(860px, calc(100vw - 32px))' : '';
  const err = dlg.querySelector('.form-error');
  form.addEventListener('click', async (e) => {
    if (e.target.closest('[data-close]')) { e.preventDefault(); dlg.close(); return; }
    if (onClick) await onClick(e, form);
  });
  if (onInput) {
    form.addEventListener('input', (e) => onInput(e, form));
    form.addEventListener('change', (e) => onInput(e, form));
  }
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!onSubmit) return dlg.close();
    const btn = form.querySelector('[type="submit"]');
    err.textContent = '';
    btn.disabled = true;
    try {
      const msg = await onSubmit(form);
      dlg.close();
      if (msg) toast(msg);
      await afterSave();
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });
  if (!dlg.open) dlg.showModal();
  const first = form.querySelector('.modal-body input:not([type=hidden]), .modal-body select');
  first?.focus();
  return form;
}

export const closeModal = () => dlg.close();

// ------------------------------------------------------------ field helpers

const HOLDING = ['asset', 'liability'];
const KIND_GROUP = { asset: 'Accounts', liability: 'Credit cards & loans', income: 'Sources', expense: 'Categories', equity: 'Bookkeeping' };
const NEW_LABEL = { income: '+ New source…', expense: '+ New category…', asset: '+ New account…', fund: '+ New fund…' };

function accountOptions(kinds, selected, { placeholder = 'Choose…', allowNew = true } = {}) {
  const sel = selected == null ? '' : String(selected);
  let html = `<option value="">${esc(placeholder)}</option>`;
  for (const kind of kinds) {
    const list = S.accounts.filter((a) => a.kind === kind && (!a.archived || String(a.id) === sel));
    if (!list.length) continue;
    const opts = list.map((a) => `<option value="${a.id}" ${String(a.id) === sel ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
    html += kinds.length > 1 ? `<optgroup label="${esc(KIND_GROUP[kind])}">${opts}</optgroup>` : opts;
  }
  if (allowNew) html += `<option value="__new:${kinds[0]}">${esc(NEW_LABEL[kinds[0]] || '+ New…')}</option>`;
  return html;
}

function fundOptions(selected, { placeholder = 'Choose fund…', allowNew = true, exclude = null } = {}) {
  const sel = selected == null ? '' : String(selected);
  let html = placeholder ? `<option value="">${esc(placeholder)}</option>` : '';
  for (const f of S.funds) {
    if ((f.archived && String(f.id) !== sel) || f.id === exclude) continue;
    html += `<option value="${f.id}" ${String(f.id) === sel ? 'selected' : ''}>${esc(f.name)}</option>`;
  }
  if (allowNew) html += '<option value="__new:fund">+ New fund…</option>';
  return html;
}

const field = (label, control, { hint = '', span = false, cls = '' } = {}) =>
  `<label class="field ${span ? 'span-2' : ''} ${cls}">${esc(label)}${hint ? ` <span class="hint">${hint}</span>` : ''}${control}</label>`;
const amountInput = (name, value, extra = '') =>
  `<input name="${name}" inputmode="decimal" autocomplete="off" placeholder="0.00" value="${esc(toInput(value))}" ${extra}>`;

/** Selecting "+ New …" in a dropdown creates the record on the spot. */
async function quickCreate(select) {
  const kind = select.value.split(':')[1];
  const label = kind === 'fund' ? 'fund' : kind === 'income' ? 'income source' : kind === 'expense' ? 'expense category' : 'account';
  const name = window.prompt(`Name of the new ${label}:`);
  if (!name || !name.trim()) { select.value = ''; return null; }
  try {
    let created;
    if (kind === 'fund') created = (await api('/funds', { method: 'POST', body: { name: name.trim() } })).fund;
    else created = (await api('/accounts', { method: 'POST', body: { name: name.trim(), kind, subtype: kind === 'asset' ? 'bank' : '' } })).account;
    await loadRefs();
    toast(`Created ${label} “${created.name}”`);
    return { kind, id: created.id };
  } catch (e) {
    toast(e.message, { error: true });
    select.value = '';
    return null;
  }
}

// --------------------------------------------------------------- transactions

const TXN_TYPES = [
  ['income', 'Income', 'Money coming in from a source (salary, freelance…). Split it into funds now, or leave it in Unallocated.'],
  ['allocation', 'Allocate', 'Give money a purpose. It stays in the same account; only its fund changes.'],
  ['expense', 'Expense', 'Money going out, paid from a fund.'],
  ['payment', 'Pay obligation', 'Pay an amortization, premium or bill. It advances that obligation’s schedule.'],
  ['transfer', 'Transfer', 'Move money between accounts (e.g. bank → e-wallet, or paying a credit card). The fund stays the same.'],
  ['opening', 'Opening balance', 'Money you already had when you started tracking.'],
  ['adjustment', 'Adjustment', 'Correct a balance to match your statement. Use a negative amount to reduce it.'],
];

function splitRow(fundId, amount) {
  return `<div class="split-row">
    <select name="split_fund" aria-label="Fund">${fundOptions(fundId)}</select>
    ${amountInput('split_amount', amount, 'aria-label="Amount"')}
    <button type="button" class="btn btn-ghost btn-sm" data-remove-split aria-label="Remove line">✕</button>
  </div>`;
}

function splitsEditor(splits, label) {
  const rows = (splits && splits.length ? splits : [{}]).map((s) => splitRow(s.fund_id, s.amount)).join('');
  return `<div class="span-2 field"><span>${esc(label)}</span>
    <div class="split-rows" data-splits>${rows}</div>
    <div class="row between wrap"><button type="button" class="btn btn-sm" data-add-split>+ Add fund</button><span class="split-summary" data-split-summary></span></div>
  </div>`;
}

function txnFields(type, v, ctx) {
  const date = field('Date', `<input type="date" name="date" value="${esc(v.date || today())}" required>`);
  const desc = field('Description', `<input name="description" value="${esc(v.description || '')}" placeholder="What is this for?">`, { span: true });
  const ref = field('Reference', `<input name="reference" value="${esc(v.reference || '')}" placeholder="Receipt / OR no. (optional)">`);
  const hint = '<p class="callout span-2" data-hint hidden></p>';
  const unalloc = S.meta.system.unallocated_fund;
  switch (type) {
    case 'income':
      return [
        field('Source', `<select name="source_id">${accountOptions(['income'], v.source_id)}</select>`),
        field('Received into', `<select name="account_id">${accountOptions(HOLDING, v.account_id)}</select>`),
        field('Amount', amountInput('amount', v.amount)), date,
        splitsEditor(v.splits, 'Put it into funds (anything left over goes to Unallocated)'),
        desc, ref,
      ].join('');
    case 'allocation':
      return [
        field('In account', `<select name="account_id">${accountOptions(HOLDING, v.account_id)}</select>`),
        field('From fund', `<select name="from_fund_id">${fundOptions(v.from_fund_id ?? unalloc)}</select>`),
        splitsEditor(v.splits || (v.to_fund_id ? [{ fund_id: v.to_fund_id, amount: v.amount }] : null), 'To funds'),
        hint, date, desc,
      ].join('');
    case 'expense':
      return [
        field('Paid from', `<select name="account_id">${accountOptions(HOLDING, v.account_id)}</select>`),
        field('Fund', `<select name="fund_id">${fundOptions(v.fund_id)}</select>`),
        field('Category', `<select name="category_id">${accountOptions(['expense'], v.category_id)}</select>`),
        field('Amount', amountInput('amount', v.amount)), hint, date, ref, desc,
      ].join('');
    case 'payment': {
      const obs = ctx.plan.obligations;
      const ob = obs.find((o) => String(o.id) === String(v.obligation_id));
      const opts = '<option value="">Choose…</option>' + obs.map((o) =>
        `<option value="${o.id}" ${String(o.id) === String(v.obligation_id) ? 'selected' : ''}>${esc(o.name)} — ${esc(money(o.amount))}</option>`).join('');
      const info = ob
        ? `<p class="callout span-2">${ob.completed ? 'This obligation is fully paid.' : `Next installment: <b>#${ob.paid_count + 1}${ob.total_payments ? ` of ${ob.total_payments}` : ''}</b>, due ${esc(fmtDate(ob.next_due))}.`}${ob.remaining_balance != null ? ` Remaining balance before this payment: <b>${esc(money(ob.remaining_balance))}</b>.` : ''}</p>`
        : '';
      if (!obs.length) return '<p class="callout span-2">No obligations yet. Add one on the Obligations page first.</p>';
      return [
        field('Obligation', `<select name="obligation_id" data-refill>${opts}</select>`, { span: true }), info,
        field('Installments covered', `<input type="number" min="1" step="1" name="installments" value="${esc(v.installments || 1)}" data-refill>`),
        field('Amount', amountInput('amount', v.amount ?? (ob ? ob.amount * (Number(v.installments) || 1) : ''))),
        field('Paid from', `<select name="account_id">${accountOptions(HOLDING, v.account_id ?? ob?.account_id)}</select>`),
        field('Fund', `<select name="fund_id">${fundOptions(v.fund_id ?? ob?.fund_id)}</select>`),
        field('Category', `<select name="category_id">${accountOptions(['expense'], v.category_id ?? ob?.category_id)}</select>`),
        hint, date, ref, desc,
      ].join('');
    }
    case 'transfer':
      return [
        field('From account', `<select name="from_account_id">${accountOptions(HOLDING, v.from_account_id)}</select>`),
        field('To account', `<select name="to_account_id">${accountOptions(HOLDING, v.to_account_id)}</select>`),
        field('Fund', `<select name="fund_id">${fundOptions(v.fund_id)}</select>`),
        field('Amount', amountInput('amount', v.amount)),
        field('Fee', amountInput('fee', v.fee), { hint: 'optional' }),
        field('Fee category', `<select name="fee_category_id">${accountOptions(['expense'], v.fee_category_id ?? S.accounts.find((a) => a.kind === 'expense' && /fee/i.test(a.name))?.id)}</select>`),
        hint, date, desc,
      ].join('');
    case 'opening':
    case 'adjustment':
      return [
        field('Account', `<select name="account_id">${accountOptions(HOLDING, v.account_id)}</select>`),
        field('Fund', `<select name="fund_id">${fundOptions(v.fund_id ?? unalloc)}</select>`),
        field(type === 'opening' ? 'Balance' : 'Change (+/−)', amountInput('amount', v.amount),
          { hint: type === 'opening' ? 'for credit cards: amount owed' : '' }),
        date, desc,
      ].join('');
    default:
      return '<p class="callout span-2">Journal entries are created through the API.</p>';
  }
}

function readSplits(form) {
  const rows = [...form.querySelectorAll('.split-row')];
  const out = [];
  for (const r of rows) {
    const fundId = r.querySelector('[name=split_fund]').value;
    const amt = parseAmount(r.querySelector('[name=split_amount]').value);
    if (!fundId && amt === null) continue;
    if (!fundId || fundId.startsWith('__')) throw new Error('Choose a fund for every line');
    if (amt === null || Number.isNaN(amt) || amt <= 0) throw new Error('Enter a valid amount for every fund line');
    out.push({ fund_id: Number(fundId), amount: amt });
  }
  return out;
}

function readValues(form, type) {
  const fd = new FormData(form);
  const get = (k) => fd.get(k);
  const id = (k) => (get(k) ? Number(get(k)) : null);
  const amt = (k, opts) => {
    const val = parseAmount(get(k), opts);
    if (Number.isNaN(val)) throw new Error(`Invalid ${k.replace('_', ' ')}: use numbers like 1,234.50`);
    return val;
  };
  const v = { type, date: get('date'), description: get('description') || '', reference: get('reference') || '' };
  switch (type) {
    case 'income':
      Object.assign(v, { source_id: id('source_id'), account_id: id('account_id'), amount: amt('amount'), splits: readSplits(form) });
      break;
    case 'allocation':
      Object.assign(v, { account_id: id('account_id'), from_fund_id: id('from_fund_id'), splits: readSplits(form) });
      break;
    case 'expense':
      Object.assign(v, { account_id: id('account_id'), fund_id: id('fund_id'), category_id: id('category_id'), amount: amt('amount') });
      break;
    case 'payment':
      Object.assign(v, {
        obligation_id: id('obligation_id'), installments: Number(get('installments')) || 1, account_id: id('account_id'),
        fund_id: id('fund_id'), category_id: id('category_id'), amount: amt('amount'),
      });
      break;
    case 'transfer':
      Object.assign(v, {
        from_account_id: id('from_account_id'), to_account_id: id('to_account_id'), fund_id: id('fund_id'),
        amount: amt('amount'), fee: amt('fee') || 0, fee_category_id: id('fee_category_id'),
      });
      break;
    case 'opening':
    case 'adjustment':
      Object.assign(v, { account_id: id('account_id'), fund_id: id('fund_id'), amount: amt('amount', { allowNegative: true }) });
      break;
    default:
  }
  return v;
}

function cellAmount(ctx, accountId, fundId) {
  const c = ctx.balances.cells.find((x) => x.account_id === Number(accountId) && x.fund_id === Number(fundId));
  return c ? c.amount : 0;
}

function updateHints(form, type, ctx) {
  const fd = new FormData(form);
  const hint = form.querySelector('[data-hint]');
  const summary = form.querySelector('[data-split-summary]');
  let splitsTotal = 0;
  for (const r of form.querySelectorAll('.split-row')) {
    const a = parseAmount(r.querySelector('[name=split_amount]').value);
    if (a > 0) splitsTotal += a;
  }
  if (summary) {
    if (type === 'income') {
      const amount = parseAmount(fd.get('amount')) || 0;
      const left = amount - splitsTotal;
      summary.innerHTML = left >= 0
        ? `${esc(money(splitsTotal))} assigned · <b>${esc(money(left))}</b> → Unallocated`
        : `<span class="neg">Splits exceed the amount by ${esc(money(-left))}</span>`;
    } else {
      summary.innerHTML = `Total: <b>${esc(money(splitsTotal))}</b>`;
    }
  }
  if (!hint) return;
  let text = '';
  let warn = false;
  const a = acct(fd.get('account_id') || fd.get('from_account_id'));
  const fundId = fd.get('fund_id') || fd.get('from_fund_id');
  const f = fund(fundId);
  if (a && f) {
    const inAcct = cellAmount(ctx, a.id, f.id);
    const total = ctx.balances.funds[f.id] || 0;
    const need = type === 'allocation' ? splitsTotal : (parseAmount(fd.get('amount')) || 0) + (parseAmount(fd.get('fee')) || 0);
    if (a.kind === 'liability') {
      text = `${esc(f.name)} has ${esc(money(total))} in total. Charging ${esc(a.name)} adds to what you owe.`;
      warn = need > total;
    } else {
      text = `<b>${esc(f.name)}</b> has <b>${esc(money(inAcct))}</b> in ${esc(a.name)} (${esc(money(total))} across all accounts).`;
      warn = need > inAcct;
    }
    if (warn && need > 0) text += ' This would overdraw it.';
  }
  hint.hidden = !text;
  hint.classList.toggle('warn', warn);
  hint.innerHTML = text;
}

export async function openTxnModal({ type = 'expense', txn = null, preset = {} } = {}) {
  await loadRefs();
  const [balances, plan] = await Promise.all([api('/balances'), api('/plan')]);
  const ctx = { balances, plan };
  let current = txn ? txn.type : type;
  let values = txn
    ? { ...(txn.form || {}), date: txn.date, description: txn.description, reference: txn.reference }
    : { date: today(), ...preset };

  const tabs = () => TXN_TYPES.map(([t, label]) =>
    `<button type="button" data-type="${t}" aria-pressed="${t === current}">${esc(label)}</button>`).join('');
  const help = () => TXN_TYPES.find(([t]) => t === current)?.[2] || '';
  const body = () => `<div class="tabs" role="group" aria-label="Transaction type">${tabs()}</div>
    <p class="type-help" data-help>${esc(help())}</p>
    <div class="form-grid" data-fields>${txnFields(current, values, ctx)}</div>`;

  const rerender = (form) => {
    form.querySelector('.tabs').innerHTML = tabs();
    form.querySelector('[data-help]').textContent = help();
    form.querySelector('[data-fields]').innerHTML = txnFields(current, values, ctx);
    updateHints(form, current, ctx);
  };
  const snapshot = (form) => {
    try { values = { ...values, ...readValues(form, current) }; } catch { /* keep partial */ }
    const fd = new FormData(form);
    for (const k of ['date', 'description', 'reference']) if (fd.has(k)) values[k] = fd.get(k);
  };

  const form = open(modalShell({
    title: txn ? `Edit transaction #${txn.id}` : 'New transaction',
    body: body(),
    submit: txn ? 'Save changes' : 'Record',
  }), {
    onClick: async (e, f) => {
      const tab = e.target.closest('[data-type]');
      if (tab) {
        snapshot(f);
        current = tab.dataset.type;
        rerender(f);
        return;
      }
      if (e.target.closest('[data-add-split]')) {
        f.querySelector('[data-splits]').insertAdjacentHTML('beforeend', splitRow());
        return;
      }
      const rm = e.target.closest('[data-remove-split]');
      if (rm) {
        const rows = f.querySelectorAll('.split-row');
        if (rows.length > 1) rm.closest('.split-row').remove();
        else rows[0].querySelectorAll('select, input').forEach((el) => { el.value = ''; });
        updateHints(f, current, ctx);
      }
    },
    onInput: async (e, f) => {
      const t = e.target;
      if (e.type === 'change' && t.tagName === 'SELECT' && t.value.startsWith('__new:')) {
        const made = await quickCreate(t);
        if (made) {
          snapshot(f);
          const name = t.name;
          if (name === 'split_fund') {
            const rows = [...f.querySelectorAll('.split-row')];
            const idx = rows.indexOf(t.closest('.split-row'));
            const splits = rows.map((r) => ({ fund_id: r.querySelector('[name=split_fund]').value, amount: parseAmount(r.querySelector('[name=split_amount]').value) }));
            splits[idx].fund_id = made.id;
            values.splits = splits;
          } else {
            values[name] = made.id;
          }
          rerender(f);
        }
        return;
      }
      if (e.type === 'change' && t.hasAttribute('data-refill')) {
        // Picking an obligation refills its defaults (account, fund, category, amount).
        const fd = new FormData(f);
        values = {
          date: fd.get('date'), description: fd.get('description'), reference: fd.get('reference'),
          obligation_id: fd.get('obligation_id'), installments: fd.get('installments'),
        };
        const ob = ctx.plan.obligations.find((o) => String(o.id) === String(values.obligation_id));
        if (ob && !values.description) values.description = ob.name;
        rerender(f);
        return;
      }
      updateHints(f, current, ctx);
    },
    onSubmit: async (f) => {
      const payload = readValues(f, current);
      if (txn) {
        await api(`/transactions/${txn.id}`, { method: 'PUT', body: payload });
        return 'Transaction updated';
      }
      await api('/transactions', { method: 'POST', body: payload });
      return `${TXN_TYPES.find(([t]) => t === current)[1]} recorded`;
    },
  });
  updateHints(form, current, ctx);
}

// ---------------------------------------------------------- transaction view

export async function openTxnDetail(id) {
  const { transaction: t, audit } = await api(`/transactions/${id}`);
  const rows = t.postings.map((p) => `<tr>
      <td>${esc(p.account_name)} <span class="muted small">${esc(p.account_kind)}</span></td>
      <td><span class="row">${`<span class="swatch" style="background:${esc(col(p.fund_color))}"></span>`}${esc(p.fund_name)}</span></td>
      <td class="num">${p.amount > 0 ? esc(money(p.amount)) : ''}</td>
      <td class="num">${p.amount < 0 ? esc(money(-p.amount)) : ''}</td></tr>`).join('');
  const history = audit.map((a) => `<li><b>${esc(a.action)}</b> <span class="muted">${esc(a.at)} UTC</span>${a.action === 'void' ? ` — ${esc(JSON.parse(a.detail).reason || 'no reason given')}` : ''}</li>`).join('');
  const body = `
    <div class="row wrap">${typeBadge(t.type)} <span class="muted">${esc(fmtDate(t.date))}</span>${t.voided_at ? '<span class="badge t-expense">Voided</span>' : ''}</div>
    ${t.description ? `<p><b>${esc(t.description)}</b></p>` : ''}
    ${t.reference ? `<p class="muted small">Ref: ${esc(t.reference)}</p>` : ''}
    ${t.voided_at ? `<p class="callout warn">Voided ${esc(t.voided_at)} UTC${t.void_reason ? ` — ${esc(t.void_reason)}` : ''}. It no longer affects any balance but stays in the ledger.</p>` : ''}
    <div class="table-wrap"><table><thead><tr><th>Account</th><th>Fund</th><th class="num">Debit (in / spent)</th><th class="num">Credit (out / from)</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr><td colspan="2">Balanced</td><td class="num">${esc(money(t.amount))}</td><td class="num">${esc(money(t.amount))}</td></tr></tfoot></table></div>
    <div><h3>History</h3><ul class="small">${history}</ul></div>`;
  const editable = !t.voided_at && t.type !== 'journal';
  open(modalShell({
    title: `Transaction #${t.id}`,
    body,
    submit: '',
    danger: editable ? '<button type="button" class="btn btn-danger" data-void>Void…</button><button type="button" class="btn" data-edit>Edit</button>' : '',
    wide: true,
  }), {
    onClick: async (e) => {
      if (e.target.closest('[data-edit]')) openTxnModal({ txn: t });
      if (e.target.closest('[data-void]')) {
        const reason = window.prompt('Why are you voiding this transaction? (kept in the audit log)');
        if (reason === null) return;
        try {
          await api(`/transactions/${t.id}/void`, { method: 'POST', body: { reason } });
          dlg.close();
          toast('Transaction voided');
          await afterSave();
        } catch (ex) { toast(ex.message, { error: true }); }
      }
    },
  });
}

// ------------------------------------------------------------------- funds

function palettePicker(selected) {
  const sel = (selected || PALETTE[0]).toLowerCase();
  const set = PALETTE.includes(sel) ? PALETTE : [...PALETTE, sel];
  return `<div class="palette-picks" role="radiogroup" aria-label="Color">${set.map((c) =>
    `<label><input type="radio" name="color" value="${c}" ${c === sel ? 'checked' : ''}><span style="background:${esc(col(c))}" title="${c}"></span></label>`).join('')}</div>`;
}

export async function openFundModal(f = null) {
  await loadRefs();
  const used = new Set(S.funds.filter((x) => !x.archived).map((x) => x.color));
  const color = f?.color || PALETTE.find((c) => !used.has(c)) || PALETTE[0];
  const body = `<div class="form-grid">
    ${field('Name', `<input name="name" value="${esc(f?.name || '')}" ${f?.is_system ? 'disabled' : ''} required placeholder="e.g. Solar Project">`, { span: true })}
    ${field('Description', `<input name="description" value="${esc(f?.description || '')}" placeholder="What is this money for?">`, { span: true })}
    ${field('Target amount', amountInput('target', f?.target), { hint: 'optional goal' })}
    ${field('Color', palettePicker(color))}
  </div>`;
  open(modalShell({ title: f ? `Edit ${f.name}` : 'New fund', body, submit: f ? 'Save' : 'Create fund' }), {
    onSubmit: async (form) => {
      const fd = new FormData(form);
      const target = parseAmount(fd.get('target'));
      if (Number.isNaN(target)) throw new Error('Invalid target amount');
      const payload = { description: fd.get('description'), color: fd.get('color'), target: target || null };
      if (!f?.is_system) payload.name = fd.get('name');
      if (f) await api(`/funds/${f.id}`, { method: 'PUT', body: payload });
      else await api('/funds', { method: 'POST', body: payload });
      return f ? 'Fund updated' : 'Fund created';
    },
  });
}

// ---------------------------------------------------------------- accounts

const ACCOUNT_TYPES = [
  ['asset:bank', 'Bank account'], ['asset:ewallet', 'E-wallet (GCash, Maya…)'], ['asset:cash', 'Cash'],
  ['asset:investment', 'Investment / time deposit'], ['liability:credit_card', 'Credit card'],
  ['liability:loan', 'Loan / payable'], ['income:', 'Income source'], ['expense:', 'Expense category'],
];

export async function openAccountModal(a = null, presetKind = 'asset:bank') {
  const current = a ? `${a.kind}:${a.subtype || ''}` : presetKind;
  const options = ACCOUNT_TYPES.map(([v, l]) => `<option value="${v}" ${v === current ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const body = `<div class="form-grid">
    ${field('Name', `<input name="name" value="${esc(a?.name || '')}" ${a?.is_system ? 'disabled' : ''} required placeholder="e.g. BDO Savings">`, { span: true })}
    ${field('Type', `<select name="type" ${a ? 'disabled' : ''}>${options}</select>`, { span: true })}
    ${field('Notes', `<input name="notes" value="${esc(a?.notes || '')}" placeholder="Account number last 4 digits, branch…">`, { span: true })}
  </div>`;
  open(modalShell({ title: a ? `Edit ${a.name}` : 'New account', body, submit: a ? 'Save' : 'Create' }), {
    onSubmit: async (form) => {
      const fd = new FormData(form);
      if (a) {
        const payload = { notes: fd.get('notes') };
        if (!a.is_system) payload.name = fd.get('name');
        await api(`/accounts/${a.id}`, { method: 'PUT', body: payload });
        return 'Saved';
      }
      const [kind, subtype] = fd.get('type').split(':');
      await api('/accounts', { method: 'POST', body: { name: fd.get('name'), kind, subtype, notes: fd.get('notes') } });
      return 'Created';
    },
  });
}

// ------------------------------------------------------------- obligations

export async function openObligationModal(ob = null) {
  await loadRefs();
  const kinds = Object.entries(KIND_LABELS).map(([k, l]) => `<option value="${k}" ${k === (ob?.kind || 'amortization') ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const freqs = Object.entries(FREQ_LABELS).map(([k, l]) => `<option value="${k}" ${k === (ob?.frequency || 'monthly') ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const premiumCat = S.accounts.find((a) => a.kind === 'expense' && /insurance|premium|amorti|loan/i.test(a.name))?.id;
  const body = `<div class="form-grid">
    ${field('Name', `<input name="name" value="${esc(ob?.name || '')}" required placeholder="e.g. Car loan, Life insurance">`, { span: true })}
    ${field('Type', `<select name="kind">${kinds}</select>`)}
    ${field('Pay every', `<select name="frequency">${freqs}</select>`)}
    ${field('Amount per payment', amountInput('amount', ob?.amount))}
    ${field('First due date', `<input type="date" name="first_due" value="${esc(ob?.first_due || today())}">`, { hint: 'of installment #1' })}
    ${field('Total installments', `<input type="number" min="1" step="1" name="total_payments" value="${esc(ob?.total_payments || '')}" placeholder="blank = ongoing">`, { hint: 'e.g. 60 for a 5-year loan' })}
    ${field('Already paid before tracking', `<input type="number" min="0" step="1" name="prior_payments" value="${esc(ob?.prior_payments || 0)}">`, { hint: 'installments' })}
    ${field('Sinking fund', `<select name="fund_id">${ob ? fundOptions(ob.fund_id, { placeholder: 'None' }) : `<option value="__create" selected>Create a dedicated fund</option>${fundOptions(null, { placeholder: 'None', allowNew: false })}`}</select>`, { hint: 'where you save for it' })}
    ${field('Pay from account', `<select name="account_id">${accountOptions(HOLDING, ob?.account_id, { placeholder: 'Choose later', allowNew: false })}</select>`)}
    ${field('Expense category', `<select name="category_id">${accountOptions(['expense'], ob?.category_id ?? premiumCat, { placeholder: 'Choose…' })}</select>`, { span: true })}
    ${field('Notes', `<input name="notes" value="${esc(ob?.notes || '')}" placeholder="Policy no., lender, terms…">`, { span: true })}
    <p class="callout span-2">FundFlow works out the remaining balance, the next due date and how much to set aside each month from the payments you record.</p>
  </div>`;
  open(modalShell({
    title: ob ? `Edit ${ob.name}` : 'New obligation', body, submit: ob ? 'Save' : 'Add obligation',
    danger: ob ? `<button type="button" class="btn btn-danger" data-archive>${ob.archived ? 'Restore' : 'Archive'}</button>` : '',
  }), {
    onInput: async (e) => {
      const t = e.target;
      if (e.type === 'change' && t.tagName === 'SELECT' && t.value.startsWith('__new:')) {
        const made = await quickCreate(t);
        if (made) {
          const kinds = made.kind === 'fund' ? null : [made.kind];
          t.innerHTML = kinds ? accountOptions(kinds, made.id) : fundOptions(made.id, { placeholder: 'None' });
        }
      }
    },
    onClick: async (e) => {
      if (e.target.closest('[data-archive]')) {
        await api(`/obligations/${ob.id}`, { method: 'PUT', body: { archived: !ob.archived } });
        dlg.close();
        toast(ob.archived ? 'Restored' : 'Archived');
        await afterSave();
      }
    },
    onSubmit: async (form) => {
      const fd = new FormData(form);
      const amount = parseAmount(fd.get('amount'));
      if (!amount || Number.isNaN(amount)) throw new Error('Enter the amount per payment');
      const fundVal = fd.get('fund_id');
      const payload = {
        name: fd.get('name'), kind: fd.get('kind'), frequency: fd.get('frequency'), amount,
        first_due: fd.get('first_due'), total_payments: fd.get('total_payments') || null,
        prior_payments: Number(fd.get('prior_payments') || 0),
        fund_id: fundVal && fundVal !== '__create' ? Number(fundVal) : null,
        create_fund: fundVal === '__create',
        account_id: fd.get('account_id') ? Number(fd.get('account_id')) : null,
        category_id: fd.get('category_id') ? Number(fd.get('category_id')) : null,
        notes: fd.get('notes'),
      };
      if (ob) {
        delete payload.create_fund;
        await api(`/obligations/${ob.id}`, { method: 'PUT', body: payload });
        return 'Obligation updated';
      }
      await api('/obligations', { method: 'POST', body: payload });
      return 'Obligation added';
    },
  });
}

/** Allocate this month's recommended set-asides from Unallocated into each sinking fund. */
export async function openFundSetAsides(plan) {
  await loadRefs();
  const balances = await api('/balances');
  const unalloc = S.meta.system.unallocated_fund;
  const byFund = new Map();
  for (const ob of plan.obligations) {
    const need = (ob.set_aside_this_month || 0) + (ob.shortfall_now || 0);
    if (!ob.fund_id || need <= 0) continue;
    const cur = byFund.get(ob.fund_id) || { fund_id: ob.fund_id, amount: 0, names: [] };
    cur.amount += need;
    cur.names.push(ob.name);
    byFund.set(ob.fund_id, cur);
  }
  const lines = [...byFund.values()];
  if (!lines.length) { toast('Nothing to set aside right now — every obligation is funded.'); return; }
  const holding = S.accounts.filter((a) => HOLDING.includes(a.kind) && !a.archived);
  const best = holding.map((a) => ({ a, v: (balances.cells.find((c) => c.account_id === a.id && c.fund_id === unalloc) || {}).amount || 0 }))
    .sort((x, y) => y.v - x.v)[0];
  const total = lines.reduce((s, l) => s + l.amount, 0);
  const body = `<p class="type-help">Moves money from a fund (usually Unallocated) into each obligation’s sinking fund, inside one account. Edit any amount before confirming.</p>
    <div class="form-grid">
      ${field('In account', `<select name="account_id">${accountOptions(HOLDING, best?.a.id, { allowNew: false })}</select>`)}
      ${field('From fund', `<select name="from_fund_id">${fundOptions(unalloc, { allowNew: false })}</select>`)}
      <div class="span-2 split-rows">${lines.map((l) => `<div class="split-row">
          <span><input type="hidden" name="split_fund" value="${l.fund_id}">${esc(fund(l.fund_id)?.name || '')} <span class="muted small">${esc(l.names.join(', '))}</span></span>
          ${amountInput('split_amount', l.amount)}<span></span></div>`).join('')}</div>
      <p class="callout span-2" data-hint>Total: <b>${esc(money(total))}</b></p>
    </div>`;
  open(modalShell({ title: 'Fund this month’s set-asides', body, submit: 'Allocate' }), {
    onSubmit: async (form) => {
      const fd = new FormData(form);
      const funds = fd.getAll('split_fund');
      const amounts = fd.getAll('split_amount');
      const splits = funds.map((f, i) => ({ fund_id: Number(f), amount: parseAmount(amounts[i]) }))
        .filter((s) => s.amount > 0);
      if (splits.some((s) => Number.isNaN(s.amount))) throw new Error('Check the amounts');
      if (!splits.length) throw new Error('Nothing to allocate');
      await api('/transactions', {
        method: 'POST',
        body: {
          type: 'allocation', date: today(), account_id: Number(fd.get('account_id')),
          from_fund_id: Number(fd.get('from_fund_id')), splits, description: 'Monthly set-aside for obligations',
        },
      });
      return 'Set-asides allocated';
    },
  });
}
