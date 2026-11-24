'use strict';
/* Chessy front end: zero dependencies. Talks to the FastAPI backend (REST + SSE). */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------- constants ---------- */
const GLYPH = { K: '♚', Q: '♛', R: '♜', B: '♝', N: '♞', P: '♟' };
const ARROW = { green: '#4cc38a', red: '#ef5b5b', blue: '#5b9cf5', yellow: '#f0c24c', orange: '#ee8a3c' };
const LABELS = {
  brilliant: ['!!', 'Brilliant'], great: ['!', 'Great'], best: ['★', 'Best'], excellent: ['✓', 'Excellent'], good: ['✓', 'Good'],
  inaccuracy: ['?!', 'Inaccuracy'], mistake: ['?', 'Mistake'], blunder: ['??', 'Blunder'], missed_win: ['✗', 'Missed win'],
};
const BADGE_COLOR = { brilliant: '#2fd3c4', great: '#5b9cf5', best: '#4cc38a', inaccuracy: '#f0b44c', mistake: '#ee8a3c', blunder: '#ef5b5b', missed_win: '#c04bd6' };
const ERRORS = ['inaccuracy', 'mistake', 'blunder', 'missed_win'];
// "Hanging" is a plain attack/defence count after the move; the engine rating also looks at what happens next. So the piece
// that just made a Best/Great/Brilliant move is not flagged (it would contradict the rating), and the markers that remain say why.
const PIECE_NAME = { K: 'king', Q: 'queen', R: 'rook', B: 'bishop', N: 'knight', P: 'pawn' };
const hangingFor = m => (m.visuals.hanging || []).filter(h => !(h.square === m.uci.slice(2, 4) && ['brilliant', 'great', 'best'].includes(m.label)));
const hangName = h => `${h.color} ${PIECE_NAME[h.piece] || 'piece'} on ${h.square}`;
const hangTip = h => `${hangName(h)} can be captured${h.reason ? ` (${h.reason})` : ''}. This is a simple attack/defence count; the engine's rating also considers what happens next.`;
// A classification icon. Symbols alone are cryptic, so every one carries its name as a tooltip and for screen readers
// (decorative = true when the name is already printed right next to it).
const badge = (k, decorative = false) => `<i class="badge b-${k}" style="min-width:18px;height:18px;font-size:10px" ${decorative ? 'aria-hidden="true"' : `role="img" title="${LABELS[k][1]}" aria-label="${LABELS[k][1]}"`}>${LABELS[k][0]}</i>`;
const EXAMPLE = `[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n[Opening "Italian Game"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7 Kxf7 7. Qf3+ Ke6 8. Nc3 Nb4 9. Qe4 c6 10. a3 Na6 11. d4 Nac7 12. Bf4 1-0`;

/* ---------- state ---------- */
const S = {
  games: [], game: null, nodes: [], fens: [], moves: [], evals: [], summary: null,
  ply: 0, flip: false, playing: null, analyzing: false, follow: true, abort: null,
  overlays: { best: true, threat: false, hang: true, heat: false },
  explore: null, sel: null, targets: [], legal: new Map(), posEval: new Map(),
  coachDraw: { arrows: [], hl: [] }, mode: 'explain', chat: [], tab: 'moves', mastersFor: null, busy: false,
  hosted: false, hostedGames: {}, runSeq: 0, libTitle: '', me: '',      // me: the username of the last import, so cards can show games from that player's side
};

/* ---------- api ---------- */
const getCode = () => { try { return localStorage.getItem('chessy.code') || ''; } catch { return ''; } };
const authHeaders = () => ({ 'Content-Type': 'application/json', 'X-Access-Code': getCode() });
function needCode() { $('#codeModal').hidden = false; setTimeout(() => $('#codeInput').focus(), 50); throw new Error('Access code required'); }
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: authHeaders(), body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401) needCode();
  let data = null; try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : res.statusText);
  }
  return data;
}
async function streamSSE(path, body, handlers, signal) {
  const res = await fetch(path, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body), signal });
  if (res.status === 401) needCode();
  if (!res.ok) {
    let d = null; try { d = (await res.json()).detail; } catch { /* empty */ }
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : res.statusText);
  }
  const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      let ev = 'message', data = '';
      for (const l of block.split('\n')) { if (l.startsWith('event: ')) ev = l.slice(7); else if (l.startsWith('data: ')) data += l.slice(6); }
      if (data && handlers[ev]) handlers[ev](JSON.parse(data));
    }
  }
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3200); }

/* ---------- FEN helpers (display only; the server owns the rules) ---------- */
const FILES = 'abcdefgh';
const sqIdx = s => (parseInt(s[1]) - 1) * 8 + FILES.indexOf(s[0]);
const sqName = i => FILES[i % 8] + (Math.floor(i / 8) + 1);
function parseFen(fen) {
  const [pos, turn, castle, ep, half, full] = fen.split(' ');
  const b = new Array(64).fill(null);
  pos.split('/').forEach((row, r) => { let f = 0; for (const ch of row) { if (/\d/.test(ch)) f += +ch; else b[(7 - r) * 8 + f++] = ch; } });
  return { b, turn, castle, ep, half: +half, full: +full };
}
function toFen(p) {
  let rows = [];
  for (let r = 7; r >= 0; r--) { let s = '', e = 0; for (let f = 0; f < 8; f++) { const c = p.b[r * 8 + f]; if (c) { if (e) { s += e; e = 0; } s += c; } else e++; } rows.push(s + (e || '')); }
  return `${rows.join('/')} ${p.turn} ${p.castle || '-'} ${p.ep || '-'} ${p.half} ${p.full}`;
}
function applyUci(fen, uci) {
  const p = parseFen(fen), from = sqIdx(uci.slice(0, 2)), to = sqIdx(uci.slice(2, 4)), promo = uci[4];
  const piece = p.b[from]; if (!piece) return fen;
  const white = piece === piece.toUpperCase(), kind = piece.toLowerCase();
  const captured = p.b[to];
  if (kind === 'p' && !captured && (from % 8) !== (to % 8)) p.b[white ? to - 8 : to + 8] = null;   // en passant
  if (kind === 'k' && Math.abs((to % 8) - (from % 8)) === 2) {                                        // castling
    const rank = Math.floor(from / 8) * 8;
    if (to % 8 === 6) { p.b[rank + 5] = p.b[rank + 7]; p.b[rank + 7] = null; } else { p.b[rank + 3] = p.b[rank]; p.b[rank] = null; }
  }
  p.b[to] = promo ? (white ? promo.toUpperCase() : promo) : piece; p.b[from] = null;
  p.ep = null;
  if (kind === 'p' && Math.abs(to - from) === 16) p.ep = sqName((to + from) / 2);
  let c = p.castle === '-' ? '' : p.castle;
  const drop = (...chars) => { c = c.split('').filter(x => !chars.includes(x)).join(''); };
  if (piece === 'K') drop('K', 'Q'); if (piece === 'k') drop('k', 'q');
  [[0, 'Q'], [7, 'K'], [56, 'q'], [63, 'k']].forEach(([s, ch]) => { if (from === s || to === s) drop(ch); });
  p.castle = c || '-';
  p.half = (kind === 'p' || captured) ? 0 : p.half + 1;
  if (p.turn === 'b') p.full++;
  p.turn = p.turn === 'w' ? 'b' : 'w';
  return toFen(p);
}

/* ---------- derived data ---------- */
const curFen = () => S.explore ? S.explore.fen : S.fens[S.ply];
const turnOf = fen => fen.split(' ')[1];
const mvAt = p => (p >= 1 ? S.moves[p - 1] : null);
function evalAt(p) {
  if (S.explore) { const x = S.explore.eval && S.explore.eval.eval; return x ? { cp: x.cp_white, mate: x.mate, win: x.win_white, terminal: S.explore.eval.terminal } : null; }
  const e = S.evals[p]; return e || null;
}
function fmtEval(e) {
  if (!e) return '–';
  if (e.terminal === 'checkmate' || Math.abs(e.cp) >= 9900) return e.mate != null ? 'M' + Math.abs(e.mate) : '#';
  if (e.mate != null) return 'M' + Math.abs(e.mate);
  return (e.cp > 0 ? '+' : '') + (e.cp / 100).toFixed(1);
}
function fmtClock(s) { if (s == null) return ''; s = Math.max(0, s); const m = Math.floor(s / 60), r = Math.floor(s % 60); return `${m}:${String(r).padStart(2, '0')}`; }
function summaryFromMoves() {
  const out = {};
  for (const side of ['white', 'black']) {
    const ms = S.moves.filter(m => m.mover === side), counts = {}, phases = {};
    ms.forEach(m => { counts[m.label] = (counts[m.label] || 0) + 1; (phases[m.phase] ||= []).push(m.accuracy); });
    out[side] = { n: ms.length, acc: ms.length ? ms.reduce((a, m) => a + m.accuracy, 0) / ms.length : null, counts, phases };
  }
  return out;
}

/* ---------- board rendering ---------- */
function xy(sq, flip = S.flip) { const f = FILES.indexOf(sq[0]), r = +sq[1] - 1; return flip ? [(7 - f) * 100, r * 100] : [f * 100, (7 - r) * 100]; }
function arrowSvg(a, flip = S.flip, k = 1) {      // k > 1 draws a bolder arrow, for the small boards
  const [x1, y1] = xy(a.from, flip).map(v => v + 50), [x2, y2] = xy(a.to, flip).map(v => v + 50);
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
  const head = 34 * (1 + (k - 1) * .5), w = 11 * k, wing = 24 * k, bx = x2 - ux * head, by = y2 - uy * head, sx = x1 + ux * 18, sy = y1 + uy * 18;
  const px = -uy, py = ux, col = ARROW[a.color] || ARROW.green;
  const pts = [[sx + px * w / 2, sy + py * w / 2], [bx + px * w / 2, by + py * w / 2], [bx + px * wing, by + py * wing], [x2 - ux * 4, y2 - uy * 4], [bx - px * wing, by - py * wing], [bx - px * w / 2, by - py * w / 2], [sx - px * w / 2, sy - py * w / 2]];
  return `<polygon points="${pts.map(p => p.join(',')).join(' ')}" fill="${col}" opacity=".82"/>`;
}
function pieceSvg(ch, x, y) {
  const white = ch === ch.toUpperCase();
  return `<text x="${x + 50}" y="${y + 74}" font-size="84" text-anchor="middle" fill="${white ? '#fff' : '#1b1b1b'}" stroke="${white ? '#2a2a2a' : '#e8e8e8'}" stroke-width="2.4" paint-order="stroke" style="font-family:'Apple Symbols','Segoe UI Symbol','DejaVu Sans',sans-serif">${GLYPH[ch.toUpperCase()]}&#xFE0E;</text>`;
}
// A small read-only board: this position, seen from `flip`'s side, with arrows
function miniBoardSvg(fen, flip, arrows) {
  const pos = parseFen(fen), parts = [];
  for (let i = 0; i < 64; i++) { const [x, y] = xy(sqName(i), flip), light = ((i % 8) + Math.floor(i / 8)) % 2 === 1; parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="${light ? 'var(--sq-light)' : 'var(--sq-dark)'}"/>`); }
  for (let i = 0; i < 64; i++) if (pos.b[i]) { const [x, y] = xy(sqName(i), flip); parts.push(pieceSvg(pos.b[i], x, y)); }
  arrows.filter(Boolean).forEach(a => parts.push(arrowSvg(a, flip, 2)));
  return `<svg viewBox="0 0 800 800" aria-hidden="true">${parts.join('')}</svg>`;
}
function currentOverlays() {
  const fen = curFen(), p = S.ply, out = { last: null, badge: null, arrows: [], hang: [], heat: null, vis: null };
  if (S.explore) {
    const L = S.explore.last; if (L) out.last = [L.slice(0, 2), L.slice(2, 4)];
    return out;
  }
  if (p >= 1) {
    const node = S.nodes[p - 1]; if (node) out.last = [node.uci.slice(0, 2), node.uci.slice(2, 4)];
    const m = mvAt(p);
    if (m) {
      if (BADGE_COLOR[m.label]) out.badge = { sq: m.uci.slice(2, 4), label: m.label };
      if (S.overlays.best && m.arrows.best) out.arrows.push(m.arrows.best);
      if (S.overlays.hang) out.hang = hangingFor(m);
      if (S.overlays.heat) out.heat = m.visuals.heatmap;
      out.vis = { ...m.visuals, hanging: hangingFor(m) };
    }
  }
  const nxt = mvAt(p + 1);
  if (S.overlays.threat && nxt && nxt.arrows.threat) out.arrows.push(nxt.arrows.threat);
  return out;
}
function renderBoard() {
  const fen = curFen(); if (!fen) return;
  const pos = parseFen(fen), ov = currentOverlays(), parts = [];
  const lastSet = new Set(ov.last || []), hl = new Set(S.coachDraw.hl);
  for (let i = 0; i < 64; i++) {
    const f = i % 8, r = Math.floor(i / 8), name = sqName(i), [x, y] = xy(name), light = (f + r) % 2 === 1;
    parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="${light ? 'var(--sq-light)' : 'var(--sq-dark)'}"/>`);
    if (lastSet.has(name)) parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="#f7e163" opacity=".5"/>`);
    if (hl.has(name)) parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="#f0c24c" opacity=".6"/>`);
    if (ov.heat) { const v = ov.heat[i]; if (v) parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="${v > 0 ? '#3b82f6' : '#ef4444'}" opacity="${Math.min(.55, Math.abs(v) * .16)}"/>`); }
    if (S.sel === name) parts.push(`<rect x="${x}" y="${y}" width="100" height="100" fill="#7c9cff" opacity=".55"/>`);
  }
  ov.hang.forEach(h => { const [x, y] = xy(h.square); parts.push(`<circle cx="${x + 50}" cy="${y + 50}" r="44" fill="transparent" stroke="#ef4444" stroke-width="7" stroke-dasharray="14 8" opacity=".9"><title>${esc(hangTip(h))}</title></circle>`); });
  // coordinates
  for (let k = 0; k < 8; k++) {
    const fileName = S.flip ? FILES[7 - k] : FILES[k], rankName = S.flip ? k + 1 : 8 - k;
    parts.push(`<text x="${k * 100 + 88}" y="794" font-size="20" font-weight="700" fill="${k % 2 === 1 ? 'var(--sq-dark)' : 'var(--sq-light)'}" text-anchor="middle">${fileName}</text>`);
    parts.push(`<text x="6" y="${k * 100 + 24}" font-size="20" font-weight="700" fill="${k % 2 === 0 ? 'var(--sq-dark)' : 'var(--sq-light)'}">${rankName}</text>`);
  }
  S.targets.forEach(t => { const [x, y] = xy(t.slice(2, 4)), cap = pos.b[sqIdx(t.slice(2, 4))];
    parts.push(cap ? `<circle cx="${x + 50}" cy="${y + 50}" r="46" fill="none" stroke="#1d2433" stroke-opacity=".45" stroke-width="9"/>` : `<circle cx="${x + 50}" cy="${y + 50}" r="15" fill="#1d2433" opacity=".35"/>`); });
  for (let i = 0; i < 64; i++) {
    const ch = pos.b[i]; if (!ch) continue;
    const [x, y] = xy(sqName(i));
    parts.push(pieceSvg(ch, x, y));
  }
  [...ov.arrows, ...S.coachDraw.arrows].forEach(a => parts.push(arrowSvg(a)));
  if (ov.badge) { const [x, y] = xy(ov.badge.sq); parts.push(`<g><title>${LABELS[ov.badge.label][1]}</title><circle cx="${x + 86}" cy="${y + 14}" r="19" fill="${BADGE_COLOR[ov.badge.label]}" stroke="#fff" stroke-width="3"/><text x="${x + 86}" y="${y + 21}" font-size="${LABELS[ov.badge.label][0].length > 1 ? 17 : 21}" font-weight="800" fill="#fff" text-anchor="middle">${LABELS[ov.badge.label][0]}</text></g>`); }
  $('#boardHost').innerHTML = `<svg id="boardSvg" viewBox="0 0 800 800" role="img" aria-label="Chess board">${parts.join('')}</svg>`;
  $('#boardSvg').addEventListener('click', onBoardClick);
  renderChips(ov.vis);
}

/* ---------- eval bar, players, chips, graph ---------- */
function renderEval() {
  const e = evalAt(S.ply), win = e ? e.win : .5;
  const h = S.flip ? (1 - win) * 100 : win * 100;
  $('#ebWhite').style.setProperty('--p', h + '%');          // height of the vertical bar; width of the horizontal one on phones
  $('#evalbar').style.background = S.flip ? '#f1f1f1' : '#262626'; $('#ebWhite').style.background = S.flip ? '#262626' : '#f1f1f1';
  const txt = fmtEval(e), whiteAhead = e ? e.cp >= 0 : true;
  const top = $('#ebTop'), bot = $('#ebBot'); top.textContent = bot.textContent = '';
  const atBottom = S.flip ? !whiteAhead : whiteAhead; (atBottom ? bot : top).textContent = txt;
  top.style.color = S.flip ? '#222' : '#ddd'; bot.style.color = S.flip ? '#ddd' : '#222';
}
function renderPlayers() {
  if (!S.game) return;
  const m = S.game.meta, sm = summaryFromMoves();
  const clock = side => { for (let p = S.ply; p >= 1; p--) { const n = S.nodes[p - 1]; if (n && (p % 2 === 1) === (side === 'white') && n.clock != null) return fmtClock(n.clock); } return ''; };
  const mk = side => `<span>${esc(m[side])}<span class="elo">${m[side + '_elo'] ?? ''}</span>${sm[side].acc != null ? `<span class="acc">${sm[side].acc.toFixed(0)}% acc</span>` : ''}</span><span class="clock">${clock(side) || '–'}</span>`;
  const bottom = S.flip ? 'black' : 'white', top = S.flip ? 'white' : 'black';
  $('#pBot').innerHTML = mk(bottom); $('#pTop').innerHTML = mk(top);
}
function renderChips(vis) {
  const el = $('#chips');
  if (!vis) { el.innerHTML = ''; return; }
  el.innerHTML = `<span class="pill">King safety <b>W ${vis.king_safety.white}</b> · <b>B ${vis.king_safety.black}</b></span><span class="pill">Mobility <b>W ${vis.mobility.white}</b> · <b>B ${vis.mobility.black}</b></span>` +
    vis.hanging.map(h => `<span class="pill" style="color:var(--bad)" title="${esc(hangTip(h))}">Hanging: ${esc(hangName(h))}${h.reason ? ` · ${esc(h.reason)}` : ''}</span>`).join('');
}
// Moves worth a dot on the graph, and how big: the errors that cost the game, the brilliant finds, and (smaller) great moves.
const GRAPH_DOTS = { brilliant: 5.5, mistake: 5.5, blunder: 5.5, missed_win: 5.5, great: 4 };
function renderGraph() {
  const g = $('#graph'), n = Math.max(1, S.fens.length - 1), H = 90;
  const W = Math.max(200, Math.round(g.getBoundingClientRect().width) || 600);      // draw at the real pixel width so dots stay round
  g.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const pts = []; for (let i = 0; i <= n; i++) { const e = S.evals[i]; if (e) pts.push([i / n * W, H * (1 - e.win)]); }
  const known = pts.length ? pts[pts.length - 1][0] : 0;
  // Black's side is dark, White's is light, and the boundary between them is the evaluation (up = Black is better, down = White).
  let svg = `<rect width="${W}" height="${H}" fill="#1e222d"/>`;
  if (pts.length > 1) svg += `<polygon points="0,${H} ${pts.map(p => p.join(',')).join(' ')} ${known},${H}" fill="#f4f5f8"/>`;
  if (known < W - 1) svg += `<rect x="${known}" width="${W - known}" height="${H}" fill="#7d88aa" opacity=".45"/>`;      // not analyzed yet: neither side
  svg += `<line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#7d88aa" stroke-width="1.5"/>`;                   // equal position
  S.moves.forEach(m => {
    const r = GRAPH_DOTS[m.label], e = S.evals[m.ply]; if (!r || !e) return;
    const x = m.ply / n * W, y = Math.min(H - 7, Math.max(7, H * (1 - e.win)));
    svg += `<g><title>${Math.ceil(m.ply / 2)}${m.ply % 2 ? '.' : '...'} ${esc(m.san)} · ${LABELS[m.label][1]}</title>` +
      `<circle cx="${x}" cy="${y}" r="${r + 1.5}" fill="none" stroke="#1e222d" stroke-width="1"/>` +             // dark rim shows on the light side...
      `<circle cx="${x}" cy="${y}" r="${r}" fill="${BADGE_COLOR[m.label]}" stroke="#fff" stroke-width="1.5"/></g>`;   // ...white rim on the dark side
  });
  svg += `<line x1="${S.ply / n * W}" y1="0" x2="${S.ply / n * W}" y2="${H}" stroke="#4f6df5" stroke-width="2"/>`;
  g.innerHTML = svg;
}

/* ---------- review tab ---------- */
function renderMoveCard() {
  const el = $('#moveCard');
  if (S.explore) { el.innerHTML = `<div class="mc-head">🔀 Exploring a side line</div><div class="muted small">Moves: ${esc((S.explore.sans || []).join(' ') || '…')}</div>`; return; }
  if (S.ply === 0) { el.innerHTML = `<div class="mc-head">Starting position</div><div class="muted">${S.moves.length ? 'Use ← → or click a move to step through the game.' : 'Press <b>Analyze game</b> to review every move.'}</div>`; return; }
  const node = S.nodes[S.ply - 1], m = mvAt(S.ply), num = Math.ceil(S.ply / 2), dots = S.ply % 2 ? '.' : '...';
  if (!m) { el.innerHTML = `<div class="mc-head">${num}${dots} ${esc(node.san)}</div><div class="muted">Not analyzed yet.</div>`; return; }
  const [sym, name] = LABELS[m.label];
  let html = `<div class="mc-head"><span class="badge b-${m.label}">${sym}</span> ${num}${dots} ${esc(m.san)} <span class="lab">${name} · ${fmtEval(m.eval_after)}</span></div><div>${esc(m.comment)}</div>`;
  const tags = [...(m.motifs.created || []).map(x => '✔ ' + x.description), ...(m.motifs.missed || []).map(x => '✘ missed: ' + x.description)];
  if (tags.length) html += `<div class="muted small">${tags.slice(0, 3).map(esc).join('<br>')}</div>`;
  if (ERRORS.includes(m.label) && m.best) html += `<button class="btn sm bestbtn" id="tryBest">Try ${esc(m.best.san)} instead</button> <span class="muted small">Line: ${esc(m.best.pv_san.join(' '))}</span>`;
  el.innerHTML = `<div class="movecard">${html}</div>`;
  const tb = $('#tryBest'); if (tb) tb.onclick = () => { S.explore = null; startExplore(S.fens[S.ply - 1], [m.best.uci]); setTab('lines'); };
}
// The turning points of the game: server-picked plies and one-sentence explanations, drawn on mini boards of the position before the move
function renderMoments() {
  const el = $('#momentsBox'), list = S.summary && S.summary.moments;
  if (!list) { el.innerHTML = ''; return; }
  const minor = list.length > 0 && list.every(x => x.minor), title = minor ? 'Biggest slips in this game' : 'Moments that decided this game';
  if (!list.length) { el.innerHTML = '<div class="card"><h3>Moments that decided this game</h3><p class="muted small" style="margin:8px 0 0">No mistakes were found in this game.</p></div>'; return; }
  const me = S.me.toLowerCase();
  const cards = list.map(({ ply, sentence }) => {
    const m = S.moves[ply - 1]; if (!m) return '';
    const name = S.game.meta[m.mover], who = me && name.toLowerCase() === me ? 'You' : name, no = `${Math.ceil(ply / 2)}${ply % 2 ? '.' : '...'} ${m.san}`;
    return `<button class="moment${S.ply === ply ? ' cur' : ''}" data-ply="${ply}" aria-label="${esc(`${LABELS[m.label][1]}, move ${no}, by ${who}. ${sentence}`)}">` +
      `<span class="mb">${miniBoardSvg(m.fen_before, m.mover === 'black', [m.arrows.played, m.arrows.best])}</span>` +
      `<span class="mt"><span class="mh"><b>${esc(no)}</b>${badge(m.label)}<span class="ml">${LABELS[m.label][1]}</span></span>` +
      `<span class="mwho"><i class="sw ${m.mover}"></i>${esc(who)}</span><span class="ms">${esc(sentence)}</span>` +
      `<span class="mlg"><span><i style="background:${ARROW.blue}"></i>Played ${esc(m.san)}</span>${m.best ? `<span><i style="background:${ARROW.green}"></i>Better ${esc(m.best.san)}</span>` : ''}</span></span></button>`;
  }).join('');
  el.innerHTML = `<div class="card"><h3>${title}</h3><div class="moments">${cards}</div></div>`;
  $$('#momentsBox [data-ply]').forEach(b => b.onclick = () => goTo(+b.dataset.ply));
}
function renderSummary() {
  const el = $('#summaryBox'); if (!S.moves.length) { el.innerHTML = ''; return; }
  const sm = summaryFromMoves(), order = ['brilliant', 'great', 'best', 'inaccuracy', 'mistake', 'blunder', 'missed_win'];
  const box = side => `<div class="accbox"><div class="who">${esc(S.game.meta[side])}</div><div class="big">${sm[side].acc != null ? sm[side].acc.toFixed(0) + '%' : '–'}</div><div class="counts">${order.map(k => `<div class="${sm[side].counts[k] ? '' : 'zero'}">${badge(k, true)}<span>${LABELS[k][1]}</span><b>${sm[side].counts[k] || 0}</b></div>`).join('')}</div></div>`;
  const sides = S.flip ? ['black', 'white'] : ['white', 'black'];
  const avg = a => a.reduce((x, y) => x + y, 0) / a.length;
  const ph = ['opening', 'middlegame', 'endgame'].map(p => {
    const rows = sides.filter(sd => (sm[sd].phases[p] || []).length).map(sd => { const v = avg(sm[sd].phases[p]); return `<div class="prow"><span class="pn"><i class="sw ${sd}"></i>${esc(S.game.meta[sd])}</span><div class="bar"><i style="width:${v}%"></i></div><span class="pv">${v.toFixed(0)}%</span></div>`; }).join('');
    return rows ? `<div class="phase"><div class="phname">${p[0].toUpperCase() + p.slice(1)}</div>${rows}</div>` : '';
  }).join('');
  const keys = (S.summary ? S.summary.key_moments : S.moves.filter(m => ERRORS.includes(m.label)).sort((a, b) => b.loss - a.loss).slice(0, 6).map(m => m.ply));
  const km = keys.map(p => { const m = S.moves[p - 1]; return m ? `<button data-ply="${p}" aria-label="${LABELS[m.label][1]}: move ${Math.ceil(p / 2)}${p % 2 ? '.' : '...'} ${esc(m.san)}">${badge(m.label)} ${Math.ceil(p / 2)}${p % 2 ? '.' : '...'} ${esc(m.san)}</button>` : ''; }).join('');
  el.innerHTML = `<div class="card"><div class="cards">${box(S.flip ? 'black' : 'white')}${box(S.flip ? 'white' : 'black')}</div>${ph ? `<h3 style="margin-top:14px">Accuracy by phase</h3>${ph}` : ''}${km ? `<h3 style="margin-top:14px">Key moments</h3><div class="keymoments">${km}</div>` : ''}</div>`;
  $$('#summaryBox [data-ply]').forEach(b => b.onclick = () => goTo(+b.dataset.ply));
}
function renderMoveList() {
  const el = $('#moveList'), rows = [];
  for (let i = 0; i < S.nodes.length; i += 2) {
    const cell = k => {
      const n = S.nodes[k]; if (!n) return '<span></span>';
      const m = S.moves[k], flagged = m && BADGE_COLOR[m.label] && m.label !== 'best';      // best/good/excellent stay plain: most moves are Best
      return `<button class="mv${flagged ? ' tint c-' + m.label : ''}${!S.explore && S.ply === k + 1 ? ' cur' : ''}" data-ply="${k + 1}">${esc(n.san)}${flagged ? badge(m.label) : ''}</button>`;
    };
    rows.push(`<span class="no">${i / 2 + 1}.</span>${cell(i)}${cell(i + 1)}`);
  }
  const keep = el.scrollTop;
  el.innerHTML = `<div class="moves-grid">${rows.join('')}</div>`;
  el.scrollTop = keep;
  $$('#moveList .mv').forEach(b => b.onclick = () => goTo(+b.dataset.ply));
  // scroll only the list itself; scrollIntoView would also scroll the page and push the board off screen
  const cur = el.querySelector('.mv.cur');
  if (cur) {
    const c = el.getBoundingClientRect(), r = cur.getBoundingClientRect();
    if (r.top < c.top) el.scrollTop += r.top - c.top;
    else if (r.bottom > c.bottom) el.scrollTop += r.bottom - c.bottom;
  }
}

/* ---------- lines tab (engine + explore) ---------- */
async function fetchEval(fen) {
  if (S.posEval.has(fen)) return S.posEval.get(fen);
  const r = await api('/api/positions/evaluate', { method: 'POST', body: { fen, moves: [], depth: 14, multipv: 4 } });
  S.posEval.set(fen, r); return r;
}
function lineRow(l) {
  const cp = l.cp ?? l.cp_white, mate = l.mate;
  const e = { cp, mate }, white = cp >= 0;
  return `<button class="line" data-uci="${l.uci}"><span class="ev ${white ? 'w' : 'b'}">${fmtEval(e)}</span><span class="pv"><b>${esc((l.pv_san || [])[0] || l.san)}</b> ${esc((l.pv_san || []).slice(1, 7).join(' '))}</span></button>`;
}
async function renderLines() {
  const host = $('#lines'), bar = $('#exploreBar'), fen = curFen();
  bar.innerHTML = S.explore ? `<div class="explorebar"><span>🔀 Side line: <b>${esc((S.explore.sans || []).join(' ') || '…')}</b></span><span class="row"><button class="btn sm" id="exUndo">Undo</button><button class="btn sm" id="exBack">Back to game</button></span></div>` : '';
  if (S.explore) { $('#exUndo').onclick = undoExplore; $('#exBack').onclick = () => { S.explore = null; resetSel(); renderAll(); }; }
  let lines = null;
  if (!S.explore && mvAt(S.ply + 1)) lines = mvAt(S.ply + 1).lines;
  else if (S.explore && S.explore.eval) lines = S.explore.eval.lines;
  else if (S.posEval.has(fen)) lines = S.posEval.get(fen).lines;
  if (!lines) {
    host.innerHTML = '<p class="muted">Thinking…</p>';
    try { const r = await fetchEval(fen); if (curFen() === fen) { if (S.explore) S.explore.eval = r; renderLines(); renderEval(); } } catch (e) { host.innerHTML = `<p class="msg">${esc(e.message)}</p>`; }
    return;
  }
  $('#linesTitle').textContent = `Engine lines · ${turnOf(fen) === 'w' ? 'White' : 'Black'} to move`;
  host.innerHTML = lines.map(lineRow).join('') || '<p class="muted">Game over.</p>';
  $$('#lines .line').forEach(b => b.onclick = () => playUci(b.dataset.uci));
}

/* ---------- explore / board interaction ---------- */
function resetSel() { S.sel = null; S.targets = []; }
async function legalFor(fen) {
  if (!S.legal.has(fen)) S.legal.set(fen, (await api('/api/positions/legal?fen=' + encodeURIComponent(fen))).legal_uci);
  return S.legal.get(fen);
}
function sqFromEvent(e) {
  const svg = $('#boardSvg'), r = svg.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * 8, y = (e.clientY - r.top) / r.height * 8;
  const col = Math.max(0, Math.min(7, Math.floor(x))), row = Math.max(0, Math.min(7, Math.floor(y)));
  return FILES[S.flip ? 7 - col : col] + ((S.flip ? row : 7 - row) + 1);
}
async function onBoardClick(e) {
  const sq = sqFromEvent(e), fen = curFen(), pos = parseFen(fen), ch = pos.b[sqIdx(sq)];
  if (S.sel && S.targets.some(t => t.slice(2, 4) === sq)) { const u = S.targets.find(t => t.slice(2, 4) === sq); return playUci(u.length === 4 && pos.b[sqIdx(S.sel)].toLowerCase() === 'p' && (sq[1] === '8' || sq[1] === '1') ? u + 'q' : u); }
  const mine = ch && ((ch === ch.toUpperCase()) === (pos.turn === 'w'));
  if (!mine) { resetSel(); return renderBoard(); }
  S.sel = sq; S.targets = [];
  renderBoard();
  try { const legal = await legalFor(fen); if (S.sel === sq && curFen() === fen) { S.targets = legal.filter(u => u.startsWith(sq)).map(u => u.length === 5 ? u.slice(0, 4) : u); renderBoard(); } } catch (err) { toast(err.message); }
}
function startExplore(baseFen, ucis) {
  S.explore = { base: baseFen, ucis: [], fen: baseFen, sans: [], last: null, eval: null };
  ucis.forEach(u => pushExplore(u, false));
  refreshExplore();
}
function pushExplore(uci, refresh = true) {
  if (!S.explore) S.explore = { base: curFen(), ucis: [], fen: curFen(), sans: [], last: null, eval: null };
  S.explore.ucis.push(uci); S.explore.fen = applyUci(S.explore.fen, uci); S.explore.last = uci; S.explore.eval = null;
  S.coachDraw = { arrows: [], hl: [] }; resetSel();
  if (refresh) refreshExplore();
}
async function playUci(uci) { pushExplore(uci); }
function undoExplore() {
  if (!S.explore) return;
  S.explore.ucis.pop();
  if (!S.explore.ucis.length) { S.explore = null; resetSel(); return renderAll(); }
  const ucis = S.explore.ucis, base = S.explore.base; S.explore = { base, ucis: [], fen: base, sans: [], last: null, eval: null };
  ucis.forEach(u => { S.explore.ucis.push(u); S.explore.fen = applyUci(S.explore.fen, u); S.explore.last = u; });
  refreshExplore();
}
async function refreshExplore() {
  renderAll(); setTabIfPlaying();
  const ex = S.explore; if (!ex) return;
  try {
    const r = await api('/api/positions/evaluate', { method: 'POST', body: { fen: ex.base, moves: ex.ucis, depth: 14, multipv: 4 } });
    if (S.explore !== ex || ex.ucis.length !== r.played.length) return;
    ex.sans = r.played; ex.fen = r.fen; ex.eval = r; S.legal.set(r.fen, r.legal_uci); S.posEval.set(r.fen, r);
    renderAll();
  } catch (e) { toast(e.message); undoExplore(); }
}
function setTabIfPlaying() { if (S.explore && S.tab === 'moves') setTab('lines'); }

/* ---------- navigation ---------- */
function goTo(p, manual = true) {
  if (!S.fens.length) return;
  p = Math.max(0, Math.min(S.fens.length - 1, p));
  if (manual) { S.follow = p === S.fens.length - 1 && S.analyzing; stopPlay(); }
  S.ply = p; S.explore = null; S.coachDraw = { arrows: [], hl: [] }; resetSel(); renderAll();
}
// ▶ means autoplay and nothing else (stepping uses ‹ ›); while it runs the same button becomes pause, and its name follows
function setPlayButton(playing) {
  const b = $('#bPlay');
  b.textContent = playing ? '⏸' : '▶';
  b.setAttribute('aria-label', playing ? 'Pause' : 'Play'); b.title = playing ? 'Pause (Space)' : 'Autoplay (Space)';
}
function stopPlay() { if (S.playing) { clearInterval(S.playing); S.playing = null; setPlayButton(false); } }
function togglePlay() {
  if (S.playing) return stopPlay();
  if (S.ply >= S.fens.length - 1) S.ply = 0;
  setPlayButton(true);
  S.playing = setInterval(() => { if (S.ply >= S.fens.length - 1) return stopPlay(); goTo(S.ply + 1, false); }, 900);
}
function renderAll() {
  renderBoard(); renderEval(); renderPlayers(); renderGraph(); renderMoments(); renderMoveCard(); renderSummary(); renderMoveList();
  if (S.tab === 'lines') renderLines();
}

/* ---------- game loading & analysis ---------- */
function mainline(tree) { const out = []; let n = tree; while (n.children && n.children.length) { n = n.children[0]; out.push(n); } return out; }
async function openGame(id, auto = false) {
  stopPlay(); if (S.abort) S.abort.abort();
  const g = S.hosted ? S.hostedGames[id] : await api('/api/games/' + id);
  Object.assign(S, { game: g, nodes: mainline(g.tree), moves: [], evals: [], summary: null, ply: 0, explore: null, chat: [], mastersFor: null, analyzing: false, follow: false, coachDraw: { arrows: [], hl: [] } });
  S.fens = [g.tree.fen, ...S.nodes.map(n => n.fen)];
  S.flip = false; resetSel();
  $('#chat').innerHTML = '<div class="bubble sys">Ask about any position. The coach checks Stockfish first, then points at the board.</div>';
  show('review'); setTab('moves'); renderAll();
  const done = g.reviews.filter(r => r.complete).sort((a, b) => b.depth - a.depth)[0];
  if (done) { $('#depthSel').value = [10, 14, 18].includes(done.depth) ? done.depth : 14; runAnalysis(done.depth, done.multipv); }
  else if (g.cache) restoreAnalysis(g);                       // hosted: finished in this tab already, don't spend the engine again
  else if (auto) runAnalysis(+$('#depthSel').value);
  else { $('#analyzeBtn').textContent = 'Analyze game'; $('#progressText').textContent = ''; }
}
function restoreAnalysis(g) {
  const c = g.cache;
  S.moves = c.moves.slice(); S.evals = c.evals.slice(); S.summary = c.summary;
  $('#depthSel').value = [10, 14, 18].includes(c.depth) ? c.depth : 14;
  $('#analyzeBtn').textContent = 'Re-analyze'; $('#progressText').textContent = 'Analysis complete. Use ← → to step through.';
  const first = S.moves.find(m => ERRORS.includes(m.label) && m.label !== 'inaccuracy');
  S.ply = first ? Math.max(0, first.ply - 1) : S.fens.length - 1;
  renderAll();
}
async function runAnalysis(depth, multipv = 3, force = false) {
  if (S.analyzing) return;
  const gid = S.game.id, run = ++S.runSeq; S.analyzing = true; S.follow = true; S.moves = []; S.evals = []; S.summary = null; S.abort = new AbortController();
  const btn = $('#analyzeBtn'), pr = $('#progress'), bar = $('#progressBar'), txt = $('#progressText');
  btn.disabled = true; pr.hidden = false; bar.style.width = '0'; txt.textContent = 'Starting engine…'; S.ply = 0; renderAll();
  try {
    await streamSSE(`/api/games/${gid}/analyze`, { depth, multipv, force, ...(S.hosted ? { pgn: S.game.pgn } : {}) }, {
      start: d => { S.evals[0] = d.eval; txt.textContent = d.replay ? 'Loading saved review…' : `Analyzing ${d.total} moves at depth ${d.depth}…`; renderEval(); },
      move: d => {
        if (S.game.id !== gid) return;
        S.moves.push(d.move); S.evals[d.move.ply] = d.move.eval_after;
        bar.style.width = (d.progress.done / d.progress.total * 100) + '%'; txt.textContent = `Move ${d.progress.done} of ${d.progress.total}`;
        if (S.follow && !S.explore) { S.ply = d.move.ply; resetSel(); }
        renderAll();
      },
      summary: d => { S.summary = d.summary; },
      done: () => {
        txt.textContent = 'Analysis complete. Use ← → to step through.';
        if (S.hosted && S.game && S.game.id === gid) S.game.cache = { depth, multipv, moves: S.moves.slice(), evals: S.evals.slice(), summary: S.summary };
      },
      error: d => { txt.textContent = ''; toast(d.message); },
    }, S.abort.signal);
  } catch (e) { if (run === S.runSeq) txt.textContent = ''; if (e.name !== 'AbortError') toast(e.message); }
  if (run !== S.runSeq) return;       // another game was opened meanwhile: that run owns the UI now
  S.analyzing = false; S.follow = false; btn.disabled = false; btn.textContent = 'Re-analyze'; pr.hidden = true;
  if (S.game && S.game.id === gid) {
    const first = S.moves.find(m => ERRORS.includes(m.label) && m.label !== 'inaccuracy');
    if (first && S.ply === S.fens.length - 1) S.ply = Math.max(0, first.ply - 1);
    renderAll();
  }
}

/* ---------- coach ---------- */
function addBubble(cls, text) { const b = document.createElement('div'); b.className = 'bubble ' + cls; b.textContent = text; const c = $('#chat'); c.appendChild(b); c.scrollTop = c.scrollHeight; return b; }
// "Verified" is only as strong as what was checked, so the tooltip says what that was for this kind of coach
function verifiedTag(g) {
  const d = g.depth ? ` (depth ${g.depth})` : '', el = document.createElement('div');
  el.className = 'vtag'; el.textContent = `✓ Verified by Stockfish${d}`;
  el.title = g.provider === 'offline'
    ? `Every sentence of this answer is built from Stockfish's own output for this position${g.depth ? `, searched to depth ${g.depth}` : ''}.`
    : `Stockfish analyzed this position${g.depth ? ` to depth ${g.depth}` : ''} before the coach answered, and every move it mentions was checked against the engine's lines. Other claims in the text are the AI's own wording.`;
  return el;
}
async function coachSend(text) {
  if (S.busy) return; S.busy = true;
  const mode = S.mode, shown = text || ({ explain: 'Explain this position.', socratic_hint: 'Give me a hint.', quiz_me: 'Quiz me.' })[mode];
  addBubble('me', shown);
  const sans = [...S.nodes.slice(0, S.ply).map(n => n.san), ...(S.explore ? (S.explore.sans || []) : [])];
  const bubble = addBubble('bot cursor', ''), chips = {}; let full = '', grounded = null;
  const body = { game_id: S.game && !S.hosted ? S.game.id : null, current_fen: curFen(), move_history: sans, user_message: text, mode, history: S.chat.slice(-10) };
  if (S.hosted && S.game) {             // no server-side game storage: send the little context the coach needs
    const lm = !S.explore && S.ply >= 1 ? mvAt(S.ply) : null;
    body.context = { white: S.game.meta.white, black: S.game.meta.black, opening: S.game.meta.opening, ...(lm ? { last_move: { san: lm.san, label: lm.label, comment: lm.comment } } : {}) };
  }
  try {
    await streamSSE('/api/coach/chat', body, {
      grounding: d => { grounded = d; $('#coachProvider').textContent = d.provider === 'offline' ? 'Offline engine coach' : `AI coach · ${d.provider}`; $('#coachProvider').classList.add('good'); },
      text: d => { full += d.delta; bubble.textContent = full; $('#chat').scrollTop = 1e9; },
      ui: d => {
        if (d.clear) S.coachDraw = { arrows: [], hl: [] };
        if (d.draw_arrows) S.coachDraw.arrows.push(...d.draw_arrows);
        if (d.highlight_squares) S.coachDraw.hl.push(...d.highlight_squares);
        renderBoard();
      },
      tool: d => { const c = document.createElement('div'); c.className = 'toolchip'; c.textContent = '🔧 ' + d.name.replace(/_/g, ' ') + '…'; $('#chat').insertBefore(c, bubble); chips[d.name] = c; },
      tool_result: d => { if (chips[d.name]) chips[d.name].textContent = '🔧 ' + d.name.replace(/_/g, ' ') + ' → ' + d.summary; },
      replace: d => { full = d.text; bubble.textContent = full; },
      notice: d => addBubble('sys', d.message),
      error: d => addBubble('sys', d.message),
    });
  } catch (e) { addBubble('sys', e.message); }
  bubble.classList.remove('cursor'); if (!full) bubble.remove(); else if (grounded) { bubble.after(verifiedTag(grounded)); $('#chat').scrollTop = 1e9; }
  S.chat.push({ role: 'user', content: shown }, { role: 'assistant', content: full || '(no reply)' });
  S.busy = false;
}

/* ---------- masters ---------- */
async function loadMasters() {
  if (!S.game || S.mastersFor === S.game.id) return;
  const box = $('#mastersBox'); box.innerHTML = '<p class="muted">Searching master games (Lichess)…</p>';
  try {
    const m = await api('/api/masters', { method: 'POST', body: { sans: S.nodes.slice(0, 60).map(n => n.san) } }); S.mastersFor = S.game.id;
    let h = `<h3>Opening</h3><p><b>${esc(m.opening || 'Unknown')}</b> ${m.eco ? `<span class="pill">${esc(m.eco)}</span>` : ''}</p>`;
    if (m.error) h += `<p class="msg">${esc(m.error)}</p>`;
    h += `<p class="muted small">Your game followed master theory for ${m.plies_in_book} plies.` + (m.left_book_ply ? ` It left theory at move ${Math.ceil(m.left_book_ply / 2)} with <b>${esc(m.played)}</b>.` : '') + '</p>';
    if (m.master_moves.length) h += `<h3>What masters played there</h3><table><tr><th>Move</th><th>Games</th><th>Share</th><th>Avg rating</th></tr>${m.master_moves.map(x => `<tr><td><b>${esc(x.san)}</b></td><td>${x.games.toLocaleString()}</td><td>${(x.share * 100).toFixed(0)}%</td><td>${x.avg_rating ?? ''}</td></tr>`).join('')}</table>`;
    if (m.games.length) h += `<h3 style="margin-top:14px">Closest master games</h3>${m.games.map(g => `<div class="kv"><a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.white)} (${g.white_elo ?? '?'}) vs ${esc(g.black)} (${g.black_elo ?? '?'})</a><span class="muted">${g.year ?? ''} · ${esc(g.result)}</span></div>`).join('')}`;
    box.innerHTML = h;
  } catch (e) { box.innerHTML = `<p class="msg">${esc(e.message)}</p>`; }
}

/* ---------- insights ---------- */
async function loadInsights(name) {
  const msg = $('#insMsg'), box = $('#insBox'); msg.textContent = ''; box.innerHTML = '';
  try {
    const d = await api(`/api/players/${encodeURIComponent(name)}/insights`);
    if (!d.games_analyzed) { msg.textContent = d.note; return; }
    const bars = obj => Object.entries(obj).map(([k, v]) => `<div class="phase"><div class="row"><span>${esc(k)}</span><span>${v}%</span></div><div class="bar"><i style="width:${v}%"></i></div></div>`).join('');
    const br = d.blunder_rate, pct = v => v == null ? '–' : (v * 100).toFixed(1) + '%';
    box.innerHTML = `<div class="insgrid">
      <div class="card"><h3>Overall</h3><div class="big" style="font-size:34px;font-weight:800">${d.overall_accuracy ?? '–'}%</div><p class="muted">${d.games_analyzed} analyzed game(s), ${d.moves_analyzed} of your moves</p></div>
      <div class="card"><h3>Accuracy by phase</h3>${bars(d.accuracy_by_phase)}<p class="muted small">Weakest: <b>${esc(d.weakest_phase || '–')}</b></p></div>
      <div class="card"><h3>Under time pressure</h3><div class="kv"><span>Blunder rate &lt; ${br.threshold_seconds}s</span><b>${pct(br.low_clock)}</b></div><div class="kv"><span>Blunder rate otherwise</span><b>${pct(br.normal_clock)}</b></div><p class="muted small">${esc(br.verdict)}</p></div>
      <div class="card"><h3>Tactics you miss</h3>${d.most_missed_motifs.map(m => `<div class="kv"><span>${esc(m.motif.replace('_', ' '))}</span><b>${m.count}</b></div>`).join('') || '<p class="muted">Nothing recurring yet.</p>'}</div>
      <div class="card"><h3>Weakest openings</h3>${d.weakest_openings.map(o => `<div class="kv"><span>${esc(o.opening)} <span class="muted">(${o.games})</span></span><b>${o.avg_accuracy ?? '–'}%</b></div>`).join('')}</div>
      ${d.advice.map(a => `<div class="card res"><h3>Work on this</h3><p><b>${esc(a.title)}</b><br><span class="muted">${esc(a.reason)}</span></p><p>${esc(a.habit)}</p>${a.links.map(l => `<div><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a></div>`).join('')}</div>`).join('')}
    </div>`;
  } catch (e) { msg.textContent = e.message; }
}

/* ---------- home / library ---------- */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// PGN dates look like 2026.11.20, or 2026.??.?? when only part is known
function fmtGameDate(d) {
  const m = /^(\d{4})\.(\d{2}|\?\?)\.(\d{2}|\?\?)$/.exec(d || ''); if (!m) return '';
  const year = +m[1], mon = MONTHS[+m[2] - 1];
  if (!mon) return String(year);                                   // only the year is known
  const day = m[3] === '??' ? '' : ` ${+m[3]}`;
  return `${mon}${day}${year === new Date().getFullYear() ? '' : (day ? ', ' : ' ') + year}`;
}
// "Blitz · 3+2": the category follows Lichess' rule (base + 40 × increment, in seconds)
function fmtTimeControl(tc) {
  if (!tc || !tc.raw) return '';
  if (tc.base == null) return /^1\//.test(tc.raw) ? 'Daily' : tc.raw;
  const inc = tc.increment || 0, est = tc.base + 40 * inc;
  const kind = est < 180 ? 'Bullet' : est < 480 ? 'Blitz' : est < 1500 ? 'Rapid' : 'Classical';
  const t = tc.base >= 60 ? `${+(tc.base / 60).toFixed(1)}` : `${tc.base}s`;
  return `${kind} · ${inc ? `${t}+${inc}` : tc.base >= 60 ? `${t} min` : t}`;
}
// Which side the imported player was on (null for pasted games or other players), and how it ended for them
function mySide(g) { const me = S.me.toLowerCase(); return !me ? null : g.white.toLowerCase() === me ? 'white' : g.black.toLowerCase() === me ? 'black' : null; }
function gameOutcome(g, side) {
  const r = g.result;
  if (r === '1/2-1/2') return ['draw', 'Draw'];
  if (r !== '1-0' && r !== '0-1') return ['', r === '*' ? 'Unfinished' : r];
  if (!side) return ['', r];
  return (r === '1-0') === (side === 'white') ? ['won', 'Won'] : ['lost', 'Lost'];
}
function gameCard(g) {
  const side = mySide(g), opp = side === 'white' ? 'black' : 'white', [oc, ot] = gameOutcome(g, side);
  const elo = p => g[p + '_elo'] ? `<span class="elo">${g[p + '_elo']}</span>` : '';
  const who = side ? `<i class="sw ${side}" title="You played ${side}"></i><span class="gc-vs">vs</span><b>${esc(g[opp])}</b>${elo(opp)}`
                   : `<b>${esc(g.white)}</b>${elo('white')}<span class="gc-vs">vs</span><b>${esc(g.black)}</b>${elo('black')}`;
  const bits = [fmtTimeControl(g.time_control), fmtGameDate(g.date || (g.meta && g.meta.date)), `${Math.ceil(g.plies / 2)} moves`].filter(Boolean);
  return `<button class="gcard" data-id="${g.id}"><span class="gc-top"><span class="gc-who">${who}</span><span class="gc-res ${oc}">${esc(ot)}</span></span>` +
    `<span class="gc-open">${esc(g.opening || 'Opening not recorded')}</span>` +
    `<span class="gc-meta">${bits.map(esc).join(' · ')}${g.analyzed ? ' · <span class="gc-done">analyzed</span>' : ''}</span></button>`;
}
async function loadLibrary() {
  if (!S.hosted) { try { S.games = (await api('/api/games')).games; } catch (e) { if (e.message !== 'Access code required') S.games = []; } }
  $('#libraryCard').hidden = !S.games.length;
  $('#libraryTitle').textContent = S.libTitle || 'Your games';
  $('#library').innerHTML = S.games.map(gameCard).join('');
  $$('#library .gcard').forEach(b => b.onclick = () => openGame(b.dataset.id, true).catch(e => toast(e.message)));
}
async function doImport(body, btn) {
  const who = (body.chesscom_username || body.lichess_username || '').trim();
  if (who) S.me = who;                    // so cards show the games from that player's side
  S.libTitle = who ? `Recent games for ${who}` : 'Your games';
  const msg = $('#importMsg'); msg.className = 'msg'; msg.textContent = ''; btn.disabled = true;
  const label = btn.textContent; if (btn.dataset.busy) btn.textContent = btn.dataset.busy;
  try {
    const r = await api('/api/games/import', { method: 'POST', body });
    if (r.hosted) { S.hosted = true; r.games.forEach(g => { S.hostedGames[g.id] = g; }); const ids = new Set(r.games.map(g => g.id)); S.games = [...r.games, ...S.games.filter(g => !ids.has(g.id))]; }
    await loadLibrary();
    if (r.games.length === 1) await openGame(r.games[0].id, true);
    else { msg.className = 'msg ok'; msg.textContent = `Imported ${r.games.length} games. Pick one below.`; }
  } catch (e) { msg.textContent = e instanceof TypeError && /fetch|network|load failed/i.test(e.message) ? "Couldn't reach Chessy's server. Check your connection and try again." : e.message; }
  btn.disabled = false; btn.textContent = label;
}
function show(view) {
  $$('.view').forEach(v => v.hidden = v.id !== 'view-' + view);
  $$('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.nav === view));
  if (view !== 'review') stopPlay();
  if (view === 'home') loadLibrary();
}
function setTab(t) {
  S.tab = t; $$('#rtabs .tab').forEach(b => b.classList.toggle('active', b.dataset.rt === t));
  ['moves', 'lines', 'coach', 'masters'].forEach(k => $('#rt-' + k).hidden = k !== t);
  if (t === 'lines') renderLines(); if (t === 'masters') loadMasters();
}

/* ---------- wire up ---------- */
// Dark by default; the button switches to light and back, and the choice is remembered (the inline script in <head> applies it before paint)
function applyTheme(theme, remember = false) {
  const light = theme === 'light';
  if (light) document.documentElement.dataset.theme = 'light'; else delete document.documentElement.dataset.theme;
  const b = $('#themeBtn'), next = light ? 'dark' : 'light';
  if (!b) return;        // a cached older index.html has no button; never let the theme code take the app down
  b.textContent = light ? '☾' : '☀\uFE0E'; b.setAttribute('aria-label', `Switch to ${next} theme`); b.title = `Switch to ${next} theme`;
  const meta = document.querySelector('meta[name=theme-color]'); if (meta) meta.content = light ? '#f3f5fa' : '#12151c';
  if (remember) { try { localStorage.setItem('chessy.theme', theme); } catch { /* private mode: the choice just won't be remembered */ } }
}
function init() {
  const themeBtn = $('#themeBtn'); if (themeBtn) themeBtn.onclick = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light', true);
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
  $$('[data-nav]').forEach(b => b.addEventListener('click', e => { e.preventDefault(); const v = b.dataset.nav; if (v === 'review' && !S.game) return toast('Import a game first.'); show(v); }));
  let site = 'chesscom';
  $$('#srcToggle .seg-btn').forEach(b => b.onclick = () => {
    site = b.dataset.site;
    $$('#srcToggle .seg-btn').forEach(x => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on); });
    $('#userInput').placeholder = (site === 'chesscom' ? 'Chess.com' : 'Lichess') + ' username'; $('#importMsg').textContent = ''; $('#userInput').focus();
  });
  $('#importForm').onsubmit = e => {
    e.preventDefault();
    const u = $('#userInput').value.trim(), msg = $('#importMsg'), name = site === 'chesscom' ? 'Chess.com' : 'Lichess';
    if (!u) { msg.className = 'msg'; msg.textContent = `Enter your ${name} username.`; $('#userInput').focus(); return; }
    doImport({ [site === 'chesscom' ? 'chesscom_username' : 'lichess_username']: u, max_games: +$('#userN').value }, $('#importUser'));
  };
  $('#advToggle').onclick = () => { const open = $('#advanced').hidden; $('#advanced').hidden = !open; $('#advToggle').setAttribute('aria-expanded', open); if (open) $('#pgnText').focus(); };
  $('#importPgn').onclick = e => { const t = $('#pgnText').value.trim(); if (!t) return toast('Paste a PGN or some moves first.'); doImport({ pgn: t }, e.target); };
  $('#loadExample').onclick = e => doImport({ pgn: EXAMPLE }, e.target);      // load, import and analyze in one click
  $('#pgnFile').onchange = async e => { const f = e.target.files[0]; if (f) $('#pgnText').value = await f.text(); };
  $('#bFirst').onclick = () => goTo(0); $('#bPrev').onclick = () => goTo(S.ply - 1); $('#bNext').onclick = () => goTo(S.ply + 1); $('#bLast').onclick = () => goTo(1e6);
  $('#bPlay').onclick = togglePlay; $('#bFlip').onclick = () => { S.flip = !S.flip; renderAll(); };
  $$('#overlays [data-ov]').forEach(c => c.onchange = () => { S.overlays[c.dataset.ov] = c.checked; renderBoard(); });
  window.addEventListener('resize', () => { if (S.fens.length) renderGraph(); });
  $('#graph').addEventListener('click', e => { const r = e.currentTarget.getBoundingClientRect(); goTo(Math.round((e.clientX - r.left) / r.width * (S.fens.length - 1))); });
  $('#analyzeBtn').onclick = () => runAnalysis(+$('#depthSel').value, 3, !!S.moves.length);
  $$('#rtabs .tab').forEach(b => b.onclick = () => setTab(b.dataset.rt));
  $$('#modeSeg button').forEach(b => b.onclick = () => { S.mode = b.dataset.mode; $$('#modeSeg button').forEach(x => x.classList.toggle('active', x === b)); if (S.mode !== 'explain') coachSend(''); });
  $('#chatForm').onsubmit = e => { e.preventDefault(); const t = $('#chatInput').value.trim(); $('#chatInput').value = ''; coachSend(t); };
  $$('[data-q]').forEach(b => b.onclick = () => coachSend(b.dataset.q));
  $('#clearDraw').onclick = () => { S.coachDraw = { arrows: [], hl: [] }; renderBoard(); };
  $('#insForm').onsubmit = e => { e.preventDefault(); const n = $('#insUser').value.trim(); if (n) { try { localStorage.setItem('chessy.user', n); } catch { /* private mode */ } loadInsights(n); } };
  try { const u = localStorage.getItem('chessy.user'); if (u) $('#insUser').value = u; } catch { /* ignore */ }
  document.addEventListener('keydown', e => {
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || $('#view-review').hidden) return;
    if (e.key === 'ArrowLeft') { goTo(S.ply - 1); e.preventDefault(); } else if (e.key === 'ArrowRight') { goTo(S.ply + 1); e.preventDefault(); }
    else if (e.key === 'Home') goTo(0); else if (e.key === 'End') goTo(1e6);
    else if (e.key === ' ') { togglePlay(); e.preventDefault(); } else if (e.key.toLowerCase() === 'f') { S.flip = !S.flip; renderAll(); }
  });
  $('#codeForm').onsubmit = e => { e.preventDefault(); try { localStorage.setItem('chessy.code', $('#codeInput').value.trim()); } catch { /* private mode */ } $('#codeModal').hidden = true; toast('Code saved. Try again.'); };
  api('/api/health').then(h => {
    const s = $('#status'); s.classList.add('ok'); $('#statusText').textContent = h.hosted ? `${h.engine || 'Stockfish'} · hosted demo` : `${h.engine} · ${h.workers} workers · coach: ${h.coach}`;
    s.title = $('#statusText').textContent;      // the full text, for when the header has to truncate it
    if (h.hosted) {
      S.hosted = true; $('[data-nav="insights"]').hidden = true; $('#hostedNote').hidden = false;
      $('#hostedDetails').innerHTML = `<li>Your games live in this browser tab only. Refresh or close it and they're gone.</li>` +
        `<li>Analysis goes up to depth ${h.max_depth} and ${h.max_plies} moves, and each visitor gets a limited number of reviews per hour.</li>` +
        `<li>Saved games, player insights and the local AI coach aren't available here. <a href="https://github.com/mountstorm/chessy-review#readme" target="_blank" rel="noopener">Run Chessy locally</a> to get them, with no limits.</li>`;
      $('#hostedMore').onclick = () => { const open = $('#hostedDetails').hidden; $('#hostedDetails').hidden = !open; $('#hostedMore').setAttribute('aria-expanded', open); $('#hostedMore').textContent = open ? 'Hide details' : 'Details'; };
      const deep = $('#depthSel option[value="18"]'); if (deep) deep.remove();
    }
    if (h.needs_code && !getCode()) { $('#codeModal').hidden = false; }
  })
    .catch(() => { $('#status').classList.add('err'); $('#statusText').textContent = 'backend offline'; })
    .finally(() => show('home'));      // after the health check, so hosted mode skips the database-backed library
}
init();
