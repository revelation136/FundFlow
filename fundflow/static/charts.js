// Sankey diagram of money flow: sources -> funds (-> funds) -> spending.
import { col, esc, money } from './util.js';

const NODE_W = 12;
const PAD = 18;

export function renderSankey(container, data) {
  const nodes = new Map();
  for (const n of data.nodes) {
    nodes.set(n.node, { ...n, inV: 0, outV: 0, ins: [], outs: [] });
  }

  // Net out reciprocal fund<->fund moves so the graph stays acyclic and readable.
  const pair = new Map();
  for (const l of data.links) {
    const key = [l.source, l.target].sort().join('|');
    const sign = l.source < l.target ? 1 : -1;
    pair.set(key, (pair.get(key) || 0) + sign * l.value);
  }
  const links = [];
  for (const [key, v] of pair) {
    if (!v) continue;
    const [a, b] = key.split('|');
    links.push(v > 0 ? { source: a, target: b, value: v } : { source: b, target: a, value: -v });
  }
  for (const l of links) {
    l.s = nodes.get(l.source);
    l.t = nodes.get(l.target);
    l.s.outs.push(l);
    l.t.ins.push(l);
    l.s.outV += l.value;
    l.t.inV += l.value;
  }
  const live = [...nodes.values()].filter((n) => n.ins.length || n.outs.length);
  if (!live.length) return false;

  // Columns: sources | funds by allocation depth | spending.
  for (const n of live) n.depth = n.type === 'in' ? 0 : 1;
  for (let i = 0; i < live.length; i++) {
    let changed = false;
    for (const l of links) {
      if (l.s.type === 'fund' && l.t.type === 'fund' && l.t.depth < l.s.depth + 1 && l.s.depth < live.length) {
        l.t.depth = l.s.depth + 1;
        changed = true;
      }
    }
    if (!changed) break;
  }
  const maxFund = Math.max(1, ...live.filter((n) => n.type === 'fund').map((n) => n.depth));
  for (const n of live) if (n.type === 'out') n.depth = maxFund + 1;
  const used = [...new Set(live.map((n) => n.depth))].sort((a, b) => a - b);
  for (const n of live) n.col = used.indexOf(n.depth);
  const ncols = used.length;
  const columns = Array.from({ length: ncols }, () => []);
  for (const n of live) {
    n.value = Math.max(n.inV, n.outV);
    columns[n.col].push(n);
  }

  // Every label sits to the right of its node, so each gap must fit a label
  // and the last column gets a reserved margin.
  const labelRoom = 190;
  const width = Math.max(container.clientWidth, (ncols - 1) * 240 + labelRoom + NODE_W);
  const height = Math.max(300, Math.max(...columns.map((c) => c.length)) * 46);
  const top = 28;
  const ky = Math.min(...columns.map((c) => (height - (c.length - 1) * PAD) / c.reduce((s, n) => s + n.value, 0)));
  const x0 = 4;
  const plotW = width - labelRoom - NODE_W - x0;
  const colX = (i) => (ncols === 1 ? x0 : x0 + (plotW * i) / (ncols - 1));

  const place = (column) => {
    const total = column.reduce((s, n) => s + n.value * ky, 0) + (column.length - 1) * PAD;
    let y = top + (height - total) / 2;
    for (const n of column) {
      n.y = y;
      n.h = Math.max(2, n.value * ky);
      y += n.h + PAD;
    }
  };
  columns[0].sort((a, b) => b.value - a.value);
  columns.forEach((c) => c.forEach((n) => { n.x = colX(n.col); }));
  place(columns[0]);
  // Order later columns by the weighted position of what flows into them.
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 1; i < ncols; i++) {
      for (const n of columns[i]) {
        const w = n.ins.reduce((s, l) => s + l.value, 0);
        n.bary = w ? n.ins.reduce((s, l) => s + (l.s.y + l.s.h / 2) * l.value, 0) / w : Infinity;
      }
      columns[i].sort((a, b) => a.bary - b.bary || b.value - a.value);
      place(columns[i]);
    }
  }

  for (const n of live) {
    n.outs.sort((a, b) => a.t.y - b.t.y);
    n.ins.sort((a, b) => a.s.y - b.s.y);
    let sy = n.y;
    for (const l of n.outs) { l.w = Math.max(1, l.value * ky); l.y0 = sy + l.w / 2; sy += l.value * ky; }
    let ty = n.y;
    for (const l of n.ins) { l.w = Math.max(1, l.value * ky); l.y1 = ty + l.w / 2; ty += l.value * ky; }
  }

  const nodeFill = (n) => (n.type === 'fund' ? col(n.color) : n.type === 'in' ? 'var(--ink-2)' : 'var(--muted)');
  const linkColor = (l) => (l.s.type === 'fund' ? col(l.s.color) : l.t.type === 'fund' ? col(l.t.color) : 'var(--muted)');
  const heads = { 0: 'Sources' };
  heads[ncols - 1] = live.some((n) => n.type === 'out') ? 'Spent on' : heads[ncols - 1];
  for (let i = 0; i < ncols; i++) {
    if (!heads[i]) heads[i] = columns[i].every((n) => n.type === 'fund') ? (i === 1 ? 'Funds' : 'Allocated to') : '';
  }

  const linkSvg = links.map((l) => {
    const a = l.s.x + NODE_W;
    const b = l.t.x;
    const m = (a + b) / 2;
    const tip = `${l.s.label} → ${l.t.label}: ${money(l.value)}`;
    return `<path class="sankey-link" d="M${a},${l.y0}C${m},${l.y0} ${m},${l.y1} ${b},${l.y1}" stroke="${esc(linkColor(l))}" stroke-width="${l.w.toFixed(2)}" data-tip="${esc(tip)}"/>`;
  }).join('');

  const nodeSvg = live.map((n) => {
    const tx = n.x + NODE_W + 6;
    const tip = `${n.label}: in ${money(n.inV)} · out ${money(n.outV)}`;
    const label = n.label.length > 26 ? n.label.slice(0, 25) + '…' : n.label;
    return `<g class="sankey-node" data-tip="${esc(tip)}">
      <rect x="${n.x}" y="${n.y}" width="${NODE_W}" height="${n.h}" rx="3" fill="${esc(nodeFill(n))}"/>
      <text x="${tx}" y="${n.y + n.h / 2}" dy="0.35em" text-anchor="start" class="sankey-label" style="paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round">${esc(label)} <tspan class="sankey-sub">${esc(money(n.value))}</tspan></text>
    </g>`;
  }).join('');

  const headSvg = Object.entries(heads).filter(([, t]) => t).map(([i, t]) => {
    return `<text class="sankey-col" x="${colX(Number(i))}" y="12">${esc(t)}</text>`;
  }).join('');

  container.innerHTML = `<svg width="${width}" height="${height + top + 10}" viewBox="0 0 ${width} ${height + top + 10}" role="img" aria-label="Money flow from sources through funds to spending">${headSvg}${linkSvg}${nodeSvg}</svg>`;
  return true;
}
