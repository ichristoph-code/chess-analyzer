/* =========================================================
   Chess Analyzer — App JS
   No external dependencies (no chess.js, no chessboard.js, no jQuery).
   The backend stores a FEN string for every move position, so we just
   parse those FENs ourselves to draw the board. Much simpler!
   ========================================================= */

// ---------------------------------------------------------------------------
// Chess piece rendering
// ---------------------------------------------------------------------------

// FEN uses uppercase for white pieces, lowercase for black.
// Convert a FEN piece letter to the local PNG filename (served from /static/img/chesspieces/).
// e.g. 'K' → 'wK.png', 'p' → 'bP.png'
function pieceImg(letter) {
  const color    = letter === letter.toUpperCase() ? 'w' : 'b';
  const img      = document.createElement('img');
  img.src        = `/static/img/chesspieces/${color}${letter.toUpperCase()}.png`;
  img.draggable  = false;
  img.style.cssText = 'width:82%;height:82%;object-fit:contain;pointer-events:none';
  return img;
}

// The standard starting position in FEN notation.
// We display this before any move is selected.
const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

// Convert the piece-placement part of a FEN string into a 64-element array.
// Index 0 = a8 (top-left from white's view), index 63 = h1 (bottom-right).
// Each element is a piece letter ('K', 'p', etc.) or null for an empty square.
//
// FEN ranks are separated by '/'. Within each rank, a digit means that many
// consecutive empty squares, and a letter is a piece.
// Example: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR'
function fenToArray(fen) {
  const placement = fen.split(' ')[0];  // only the board part, ignore turn/castling/etc.
  const board = [];
  for (const rank of placement.split('/')) {
    for (const ch of rank) {
      if (ch >= '1' && ch <= '8') {
        // A digit means that many empty squares in a row
        for (let i = 0; i < +ch; i++) board.push(null);
      } else {
        board.push(ch);  // a piece letter
      }
    }
  }
  return board;  // always 64 elements
}

// Return a Set of square indices (0-63) where the piece changed between two FEN positions.
// We use this to highlight the "from" and "to" squares of the last move.
function changedSquares(fenBefore, fenAfter) {
  const before = fenToArray(fenBefore);
  const after  = fenToArray(fenAfter);
  const changed = new Set();
  for (let i = 0; i < 64; i++) {
    if (before[i] !== after[i]) changed.add(i);
  }
  return changed;
}

// ---------------------------------------------------------------------------
// Stockfish best-move arrow
// ---------------------------------------------------------------------------

// Convert a square name like "g1" to a board array index (0=a8, 63=h1).
function uciSqToIdx(sq) {
  const file = sq.charCodeAt(0) - 97;   // 'a'=0 … 'h'=7
  const rank = 8 - parseInt(sq[1], 10); // '1'=7, '8'=0
  return rank * 8 + file;
}

// Draw a green arrow on the board SVG overlay for a Stockfish best-move UCI
// string like "g1f3". Clears any existing arrow first.
// orientation: 'white' | 'black'
function drawBestMoveArrow(bestMoveUci, orientation) {
  const existing = document.getElementById('best-move-svg');
  if (existing) existing.remove();
  if (!bestMoveUci || bestMoveUci.length < 4) return;

  const fromIdx = uciSqToIdx(bestMoveUci.slice(0, 2));
  const toIdx   = uciSqToIdx(bestMoveUci.slice(2, 4));

  // Map board indices to visual grid positions based on orientation
  const fromVis = orientation === 'black' ? 63 - fromIdx : fromIdx;
  const toVis   = orientation === 'black' ? 63 - toIdx   : toIdx;

  const fromRow = Math.floor(fromVis / 8), fromCol = fromVis % 8;
  const toRow   = Math.floor(toVis   / 8), toCol   = toVis   % 8;

  // 800×800 viewBox — each square is 100 units wide/tall
  const x1 = fromCol * 100 + 50;
  const y1 = fromRow * 100 + 50;
  const x2 = toCol   * 100 + 50;
  const y2 = toRow   * 100 + 50;

  // Shorten the shaft so the arrowhead tip lands at the square center
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  const shorten = 30; // units to pull back before arrowhead
  const scale   = Math.max(0, (len - shorten)) / len;
  const ex = x1 + dx * scale;
  const ey = y1 + dy * scale;

  const NS  = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.id = 'best-move-svg';
  svg.setAttribute('viewBox', '0 0 800 800');
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.style.cssText =
    'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10';

  // Arrowhead marker
  const defs   = document.createElementNS(NS, 'defs');
  const marker = document.createElementNS(NS, 'marker');
  marker.setAttribute('id', 'sf-head');
  marker.setAttribute('markerWidth', '5');
  marker.setAttribute('markerHeight', '5');
  marker.setAttribute('refX', '4.5');
  marker.setAttribute('refY', '2.5');
  marker.setAttribute('orient', 'auto');
  const head = document.createElementNS(NS, 'polygon');
  head.setAttribute('points', '0 0, 5 2.5, 0 5');
  head.setAttribute('fill', 'rgba(34,197,94,0.92)');
  marker.appendChild(head);
  defs.appendChild(marker);
  svg.appendChild(defs);

  // Origin circle (marks the from-square)
  const circ = document.createElementNS(NS, 'circle');
  circ.setAttribute('cx', x1);
  circ.setAttribute('cy', y1);
  circ.setAttribute('r', '20');
  circ.setAttribute('fill', 'rgba(34,197,94,0.45)');
  svg.appendChild(circ);

  // Arrow shaft
  const line = document.createElementNS(NS, 'line');
  line.setAttribute('x1', x1);
  line.setAttribute('y1', y1);
  line.setAttribute('x2', ex);
  line.setAttribute('y2', ey);
  line.setAttribute('stroke', 'rgba(34,197,94,0.85)');
  line.setAttribute('stroke-width', '16');
  line.setAttribute('stroke-linecap', 'round');
  line.setAttribute('marker-end', 'url(#sf-head)');
  svg.appendChild(line);

  document.getElementById('board').appendChild(svg);
}

// ---------------------------------------------------------------------------
// Draw the board into the #board div.
//   fen:              position to display
//   orientation:      'white' (a1 at bottom-left) or 'black' (a1 at top-right)
//   highlightSquares: Set of square indices to tint yellow (last move from/to)
function renderBoard(fen, orientation, highlightSquares) {
  const pieces = fenToArray(fen);
  const el = document.getElementById('board');
  el.innerHTML = '';

  // Build the list of 64 square indices in the order we want to render them.
  // White orientation: square 0 (a8) top-left → square 63 (h1) bottom-right.
  // Black orientation: square 63 (h1) top-left → square 0 (a8) bottom-right (flipped).
  const order = orientation === 'black'
    ? Array.from({length: 64}, (_, i) => 63 - i)
    : Array.from({length: 64}, (_, i) => i);

  for (const sqIdx of order) {
    const rank = Math.floor(sqIdx / 8);  // 0 = rank 8 (top), 7 = rank 1 (bottom)
    const file = sqIdx % 8;              // 0 = file a (left), 7 = file h (right)

    // A square is light when rank+file is even.
    // Check: a8 = rank 0, file 0 → even → light ✓
    //        h8 = rank 0, file 7 → odd  → dark  ✓
    //        a1 = rank 7, file 0 → odd  → dark  ✓ (a1 is always dark in chess)
    const isLight = (rank + file) % 2 === 0;
    const isHL    = highlightSquares && highlightSquares.has(sqIdx);

    const sq = document.createElement('div');
    sq.className = 'sq' +
      (isLight ? ' light' : ' dark') +
      (isHL    ? ' highlight' : '');

    const piece = pieces[sqIdx];
    if (piece) {
      sq.appendChild(pieceImg(piece));
    }

    el.appendChild(sq);
  }
}

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

let currentGame      = null;     // full game object returned from /api/game/<id>
let currentMoveIdx   = 0;        // 0 = start position, n = after n-th move in the list
let boardOrientation = 'white';  // 'white' or 'black'; toggled by the flip button
let pollTimer        = null;     // setInterval handle while waiting for background analysis

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  renderBoard(STARTING_FEN, 'white', null);
  loadGameList();
  loadCoach();

  // Wire up eval graph click-to-seek
  const canvas = document.getElementById('eval-graph');
  if (canvas) {
    canvas.addEventListener('click', e => {
      if (!currentGame || !currentGame.moves) return;
      const rect  = canvas.getBoundingClientRect();
      const x     = e.clientX - rect.left;
      const n     = currentGame.moves.length;
      const idx   = Math.min(n - 1, Math.max(0, Math.floor(x / (rect.width / n))));
      goToMove(idx + 1);
    });
  }

  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft')  stepMove(-1);
    if (e.key === 'ArrowRight') stepMove(1);
    if (e.key === 'ArrowUp')    goToMove(0);
    if (e.key === 'ArrowDown')  goToLastMove();
  });
});


// ---------------------------------------------------------------------------
// Game list
// ---------------------------------------------------------------------------

async function loadGameList() {
  const res   = await fetch('/api/games');
  const games = await res.json();
  renderGameList(games);
}

function renderGameList(games) {
  const el = document.getElementById('game-list');
  if (!games.length) {
    el.innerHTML = '<div style="padding:20px;color:var(--muted);font-size:13px">No games yet — press "↓ Fetch"</div>';
    return;
  }

  el.innerHTML = games.map(g => {
    const date     = g.end_time ? new Date(g.end_time * 1000).toLocaleDateString() : '';
    const analyzed = g.analyzed ? '<span class="analyzed-dot" title="Analyzed"></span>' : '';
    const opening  = g.opening  ? `<div class="game-opening">${escHtml(g.opening)}</div>` : '';
    return `
      <div class="game-item" onclick="loadGame('${g.id}')" data-id="${g.id}">
        <div class="game-item-header">
          <span class="result-badge result-${g.result}">${g.result.toUpperCase()}</span>
          <span class="game-opponent">${escHtml(g.opponent)}</span>
          ${analyzed}
        </div>
        <div class="game-meta">${g.time_class} · ${g.played_as} · ${date}</div>
        ${opening}
      </div>`;
  }).join('');
}

async function fetchGames() {
  const btn = document.getElementById('btn-fetch');
  btn.disabled    = true;
  btn.textContent = '…';
  try {
    const res  = await fetch('/api/games/fetch', { method: 'POST' });
    const data = await res.json();
    if (data.error) { toast(data.error); return; }
    toast(`Fetched ${data.total} games, ${data.added} new`);
    loadGameList();
  } catch (e) {
    toast('Fetch failed: ' + e.message);
  } finally {
    btn.disabled    = false;
    btn.textContent = '↓ Fetch';
  }
}

// ---------------------------------------------------------------------------
// Load & display a game
// ---------------------------------------------------------------------------

async function loadGame(gameId) {
  // Mark the selected game in the sidebar
  document.querySelectorAll('.game-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === gameId);
  });

  stopPolling();

  // Show analyzing banner immediately while we wait for the fetch — avoid
  // flashing an empty game-view with no moves during the round trip.
  document.getElementById('placeholder').style.display      = 'none';
  document.getElementById('game-view').style.display        = 'none';
  document.getElementById('analyzing-banner').style.display = 'flex';
  document.getElementById('analyzing-banner').querySelector('span + *') &&
    (document.getElementById('analyzing-banner').lastChild.textContent = 'Analyzing with Stockfish…');
  document.getElementById('move-list').innerHTML            = '';
  clearExplanation();

  const res  = await fetch(`/api/game/${gameId}`);
  const game = await res.json();
  currentGame = game;

  boardOrientation = game.played_as === 'black' ? 'black' : 'white';

  if (game.analyzing || !game.moves || !game.moves.length) {
    // Stockfish still running — keep full spinner, poll for completion
    pollTimer = setInterval(() => checkAnalysisStatus(gameId), 4000);
    return;
  }

  // Stockfish done — show the board immediately
  renderGame(game);

  if (game.commentary_pending) {
    // Claude commentary still generating — poll quietly until it arrives
    pollTimer = setInterval(() => checkCommentaryStatus(gameId), 5000);
  }
}

async function checkAnalysisStatus(gameId) {
  // Poll until Stockfish analysis is complete (analyzed=1)
  const res  = await fetch(`/api/game/${gameId}/status`);
  const data = await res.json();
  if (data.analyzed) {
    stopPolling();
    playChime();
    loadGameList();
    // Re-fetch full game data and render board
    const res2  = await fetch(`/api/game/${gameId}`);
    const game2 = await res2.json();
    currentGame = game2;
    renderGame(game2);
    if (game2.commentary_pending) {
      pollTimer = setInterval(() => checkCommentaryStatus(gameId), 5000);
    }
  }
}

async function checkCommentaryStatus(gameId) {
  // Poll until Claude commentary is ready (claude_ok=1)
  const res  = await fetch(`/api/game/${gameId}`);
  const game = await res.json();
  if (!game.commentary_pending && game.claude_ok) {
    stopPolling();
    playChime();
    // Update move explanations in currentGame and refresh display
    currentGame = game;
    updateCommentary(game.moves);
  }
}

function updateCommentary(moves) {
  // Patch explanations into existing move buttons without full re-render
  currentGame.moves = moves;
  // Refresh whatever move is currently shown
  if (currentMoveIdx > 0 && currentMoveIdx <= moves.length) {
    showExplanation(moves[currentMoveIdx - 1], currentGame.played_as);
  }
  // Hide commentary-loading indicator
  document.getElementById('commentary-banner').style.display = 'none';
}

function playChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Three ascending notes — C5, E5, G5 — played 120ms apart
    [[523.25, 0], [659.25, 0.12], [783.99, 0.24]].forEach(([freq, delay]) => {
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = 'sine';
      osc.frequency.value = freq;
      const t = ctx.currentTime + delay;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.25, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 1.4);
      osc.start(t);
      osc.stop(t + 1.4);
    });
  } catch (e) { /* audio not available — fail silently */ }
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function renderGame(game) {
  // Show the game view, hide the spinner
  document.getElementById('analyzing-banner').style.display = 'none';
  document.getElementById('game-view').style.display        = 'flex';

  currentMoveIdx = 0;
  renderBoard(STARTING_FEN, boardOrientation, null);
  renderMoveList(game.moves, game.played_as);
  updateMoveCounter();
  clearExplanation();

  // Opening name in analysis header
  const openingEl = document.getElementById('game-opening-label');
  if (game.opening) {
    openingEl.textContent   = game.opening + (game.eco ? ` (${game.eco})` : '');
    openingEl.style.display = '';
  } else {
    openingEl.textContent   = '';
    openingEl.style.display = 'none';
  }

  // Re-analyze button
  const reanalyzeBtn = document.getElementById('btn-reanalyze');
  reanalyzeBtn.style.display  = '';
  reanalyzeBtn.textContent    = '↺ Re-analyze';

  // Commentary loading banner: shown while Claude is still generating
  const commentaryBanner = document.getElementById('commentary-banner');
  if (commentaryBanner) {
    commentaryBanner.style.display = game.commentary_pending ? 'flex' : 'none';
  }

  // Warning if Claude failed (no explanations at all and not pending)
  const hasExplanations = game.moves.some(m => m.explanation);
  document.getElementById('analysis-error').style.display =
    (hasExplanations || game.commentary_pending) ? 'none' : 'flex';

  // Draw eval graph — defer one frame so the canvas has real layout dimensions
  requestAnimationFrame(() => {
    drawEvalGraph(game.moves, game.played_as, 0);
  });

  // Jump to critical moment — also deferred so move buttons exist in DOM
  requestAnimationFrame(() => {
    try {
      const critIdx = findCriticalMoment(game.moves, game.played_as);
      if (critIdx >= 0) {
        markCriticalMoment(critIdx);
        goToMove(critIdx + 1);
      }
    } catch (e) {
      console.warn('Critical moment jump failed:', e);
    }
  });
}

// ---------------------------------------------------------------------------
// Flip board
// ---------------------------------------------------------------------------

function flipBoard() {
  boardOrientation = boardOrientation === 'white' ? 'black' : 'white';

  // Re-render the board at whatever position we're currently at
  let fen   = STARTING_FEN;
  let hl    = null;
  let bmUci = null;
  if (currentGame && currentGame.moves) {
    const moves = currentGame.moves;
    if (currentMoveIdx > 0) {
      const move = moves[currentMoveIdx - 1];
      fen = move.fen;
      if (move.fen_before) hl = changedSquares(move.fen_before, move.fen);
    }
    bmUci = moves[currentMoveIdx] ? (moves[currentMoveIdx].best_move_uci || null) : null;
  }
  renderBoard(fen, boardOrientation, hl);
  drawBestMoveArrow(bmUci, boardOrientation);
}

// ---------------------------------------------------------------------------
// Move list rendering
// ---------------------------------------------------------------------------

function renderMoveList(moves, playedAs) {
  const el = document.getElementById('move-list');
  let html     = '';
  let pairOpen = false;

  moves.forEach((m, idx) => {
    // Only classify the player's own moves — opponent moves show as neutral
    const cls    = m.color === playedAs ? m.classification : 'best';
    const symbol = cls === 'blunder' ? '??' : cls === 'mistake' ? '?' : '';
    const clsCls = cls !== 'best' ? cls : '';
    const dataSym = symbol ? `data-symbol="${symbol}"` : '';

    if (m.color === 'white') {
      html += `<div class="move-pair">
        <span class="move-num">${m.move_number}.</span>
        <button class="move-btn ${clsCls}" data-idx="${idx}" ${dataSym}
          onclick="goToMove(${idx + 1})">${escHtml(m.san)}</button>`;
      pairOpen = true;
    } else {
      if (!pairOpen) {
        // Black moved first (rare, e.g. game loaded mid-way)
        html += `<div class="move-pair"><span class="move-num">…</span>`;
      }
      html += `<button class="move-btn ${clsCls}" data-idx="${idx}" ${dataSym}
        onclick="goToMove(${idx + 1})">${escHtml(m.san)}</button>
      </div>`;
      pairOpen = false;
    }
  });

  if (pairOpen) html += '</div>';  // close an unclosed white-only final move
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Move navigation
// ---------------------------------------------------------------------------

// Jump to a specific position by index.
// idx 0 = starting position (before any moves).
// idx n = position after the n-th move in the moves array.
function goToMove(idx) {
  if (!currentGame || !currentGame.moves) return;
  const moves  = currentGame.moves;
  const target = Math.max(0, Math.min(idx, moves.length));

  currentMoveIdx = target;

  // The backend pre-computed the FEN for each position — no chess logic needed here.
  let fen = STARTING_FEN;
  let hl  = null;

  if (target > 0) {
    const move = moves[target - 1];
    fen = move.fen;
    // Highlight the squares that changed to show where the piece moved from/to
    if (move.fen_before) {
      hl = changedSquares(move.fen_before, move.fen);
    }
  }

  renderBoard(fen, boardOrientation, hl);

  // Arrow: best move from the currently displayed position.
  // moves[target] holds the best_move_uci computed from the position we just
  // rendered (its fen_before == the displayed fen).
  const bmUci = moves[target] ? (moves[target].best_move_uci || null) : null;
  drawBestMoveArrow(bmUci, boardOrientation);

  updateMoveHighlight(target - 1);
  updateMoveCounter();
  drawEvalGraph(moves, currentGame.played_as, target);  // redraw cursor

  if (target > 0) {
    showExplanation(moves[target - 1], currentGame.played_as);
    scrollMoveIntoView(target - 1);
  } else {
    clearExplanation();
  }

  // Reset chat when navigating to a different move
  const newKey = `${currentGame.id}:${target - 1}`;
  if (newKey !== chatMoveKey) {
    resetChat();
    chatMoveKey = newKey;
  }
}

function stepMove(delta) {
  if (!currentGame || !currentGame.moves) return;
  goToMove(currentMoveIdx + delta);
}

function goToLastMove() {
  if (!currentGame || !currentGame.moves) return;
  goToMove(currentGame.moves.length);
}

function updateMoveHighlight(activeIdx) {
  document.querySelectorAll('.move-btn').forEach(btn => {
    btn.classList.toggle('current', parseInt(btn.dataset.idx) === activeIdx);
  });
}

function updateMoveCounter() {
  if (!currentGame || !currentGame.moves) return;
  const total = currentGame.moves.length;
  document.getElementById('move-counter').textContent =
    currentMoveIdx === 0     ? 'Start' :
    currentMoveIdx === total ? 'End'   :
    `Move ${currentMoveIdx} / ${total}`;
}

function scrollMoveIntoView(idx) {
  const btn = document.querySelector(`.move-btn[data-idx="${idx}"]`);
  if (btn) btn.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ---------------------------------------------------------------------------
// Re-analyze
// ---------------------------------------------------------------------------

async function reAnalyzeGame() {
  if (!currentGame) return;
  const btn = document.getElementById('btn-reanalyze');
  btn.disabled = true;
  btn.textContent = '…';

  await fetch(`/api/game/${currentGame.id}/analyze`, { method: 'POST' });

  document.getElementById('analyzing-banner').style.display = 'block';
  document.getElementById('game-view').style.display        = 'none';
  stopPolling();
  pollTimer = setInterval(() => checkAnalysisStatus(currentGame.id), 4000);

  btn.disabled    = false;
  btn.textContent = '↺ Re-analyze';
}

async function reAnalyzeAll() {
  const btn = document.getElementById('btn-reanalyze-all');
  btn.disabled = true;

  // Fetch the full game list
  const res   = await fetch('/api/games');
  const games = await res.json();
  if (!games.length) {
    toast('No games to re-analyze. Fetch games first.');
    btn.disabled = false;
    return;
  }

  const analyzed = games.filter(g => g.analyzed);
  if (!analyzed.length) {
    toast('No analyzed games yet.');
    btn.disabled = false;
    return;
  }

  toast(`Queuing ${analyzed.length} games for re-analysis…`);
  btn.textContent = `↺ Queuing…`;

  // Fire-and-forget each game — the server processes them in background threads
  for (const g of analyzed) {
    await fetch(`/api/game/${g.id}/analyze`, { method: 'POST' });
  }

  toast(`${analyzed.length} games queued. Analysis runs in the background.`);
  btn.textContent = '↺ Re-analyze All';
  btn.disabled    = false;
  loadGameList();   // refresh the list to show analyzing dots
}

// ---------------------------------------------------------------------------
// Eval graph
// ---------------------------------------------------------------------------

function drawEvalGraph(moves, playedAs, currentIdx) {
  const canvas = document.getElementById('eval-graph');
  if (!canvas || !moves || !moves.length) return;

  const dpr  = window.devicePixelRatio || 1;
  const rect  = canvas.getBoundingClientRect();
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;

  const ctx  = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const w    = rect.width;
  const h    = rect.height;
  const MAX  = 500;   // cap at ±5 pawns
  const midY = h / 2;
  const n    = moves.length;
  const xStep = w / n;

  // eval_after is stored from the mover's perspective (flips sign each move).
  // Normalise to white's POV first, then flip to the player's POV.
  const evals = moves.map(m => {
    let e = m.eval_after ?? 0;
    if (m.color === 'black') e = -e;          // black mover → white's POV
    e = Math.max(-MAX, Math.min(MAX, e));
    return playedAs === 'black' ? -e : e;     // flip to player's POV
  });

  function evalToY(e) { return midY - (e / MAX) * (midY - 3); }

  // Background
  ctx.fillStyle = '#0f0f1a';
  ctx.fillRect(0, 0, w, h);

  // Center line
  ctx.strokeStyle = '#2a2a4a';
  ctx.lineWidth   = 1;
  ctx.beginPath();
  ctx.moveTo(0, midY);
  ctx.lineTo(w, midY);
  ctx.stroke();

  if (n === 0) return;

  // Build the eval polyline path (shared)
  function buildPath() {
    ctx.beginPath();
    ctx.moveTo(0, midY);
    for (let i = 0; i < n; i++) {
      ctx.lineTo((i + 0.5) * xStep, evalToY(evals[i]));
    }
    ctx.lineTo(n * xStep, midY);
    ctx.closePath();
  }

  // Green fill: winning region (clip to above midY)
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, midY);
  ctx.clip();
  buildPath();
  ctx.fillStyle = 'rgba(34,197,94,0.28)';
  ctx.fill();
  ctx.restore();

  // Red fill: losing region (clip to below midY)
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, midY, w, midY);
  ctx.clip();
  buildPath();
  ctx.fillStyle = 'rgba(239,68,68,0.28)';
  ctx.fill();
  ctx.restore();

  // Eval line
  ctx.beginPath();
  ctx.moveTo(0.5 * xStep, evalToY(evals[0]));
  for (let i = 1; i < n; i++) ctx.lineTo((i + 0.5) * xStep, evalToY(evals[i]));
  ctx.strokeStyle = 'rgba(255,255,255,0.4)';
  ctx.lineWidth   = 1;
  ctx.stroke();

  // Blunder / mistake dots
  moves.forEach((m, i) => {
    if (m.color !== playedAs) return;
    const cls = m.classification;
    if (cls !== 'blunder' && cls !== 'mistake') return;
    const x = (i + 0.5) * xStep;
    const y = evalToY(evals[i]);
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = cls === 'blunder' ? '#ef4444' : '#f97316';
    ctx.fill();
  });

  // Cursor line (current move)
  if (currentIdx > 0 && currentIdx <= n) {
    const cx = (currentIdx - 0.5) * xStep;
    ctx.strokeStyle = '#7c3aed';
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, h);
    ctx.stroke();
  }
}

// ---------------------------------------------------------------------------
// Critical moment detection
// ---------------------------------------------------------------------------

function findCriticalMoment(moves, playedAs) {
  let maxDelta = 150;   // minimum threshold (1.5 pawns) to be "critical"
  let critIdx  = -1;
  moves.forEach((m, i) => {
    if (m.color !== playedAs) return;
    const d = m.delta ?? 0;
    if (d > maxDelta) { maxDelta = d; critIdx = i; }
  });
  return critIdx;
}

function markCriticalMoment(idx) {
  // Add a ⚡ indicator to the critical move button
  const btn = document.querySelector(`.move-btn[data-idx="${idx}"]`);
  if (btn) {
    btn.title = '⚡ Critical moment — biggest turning point';
    btn.classList.add('critical');
  }
}

// ---------------------------------------------------------------------------
// Explanation panel
// ---------------------------------------------------------------------------

function showExplanation(move, playedAs) {
  const isMyMove = move.color === playedAs;
  const cls      = isMyMove ? (move.classification || 'best') : 'opponent';

  // ── Header ──
  const label = document.getElementById('analysis-move-label');
  const badge = document.getElementById('classification-badge');
  const dot   = move.color === 'black' ? '…' : '.';
  label.textContent = `${move.move_number}${dot} ${move.san}`;

  badge.className   = 'badge';
  if (cls === 'blunder')    { badge.className += ' badge-blunder';    badge.textContent = 'Blunder ??'; }
  else if (cls === 'mistake')    { badge.className += ' badge-mistake';    badge.textContent = 'Mistake ?'; }
  else if (cls === 'inaccuracy') { badge.className += ' badge-inaccuracy'; badge.textContent = 'Inaccuracy'; }
  else if (cls === 'opponent')   { badge.className += ' badge-opponent';   badge.textContent = 'Opponent'; }
  else                           { badge.className += ' badge-best';       badge.textContent = 'Good'; }

  // ── Comment (with brief fade) ──
  const commentEl = document.getElementById('analysis-comment');
  commentEl.style.opacity = '0';
  const isPending = currentGame && currentGame.commentary_pending;
  setTimeout(() => {
    if (move.explanation) {
      commentEl.textContent = move.explanation;
      commentEl.classList.remove('muted');
    } else if (isPending) {
      commentEl.textContent = '⏳ Commentary loading…';
      commentEl.classList.add('muted');
    } else if (!isMyMove) {
      commentEl.textContent = `Navigate with ← → to see commentary on this move.`;
      commentEl.classList.add('muted');
    } else {
      const deltaP = move.delta ? (Math.abs(move.delta) / 100).toFixed(1) : null;
      commentEl.textContent = deltaP && cls !== 'best'
        ? `${cls.charAt(0).toUpperCase() + cls.slice(1)} — lost about ${deltaP} pawns of advantage.`
        : 'No detailed commentary available for this move.';
      commentEl.classList.add('muted');
    }
    commentEl.style.opacity = '1';
  }, 80);

  // ── Footer: best move ──
  const bestEl  = document.getElementById('analysis-best-move');
  const bestSan = document.getElementById('analysis-best-move-san');
  if (isMyMove && move.best_move && move.best_move !== move.san && cls !== 'best') {
    bestSan.textContent   = move.best_move;
    bestEl.style.display  = 'flex';
  } else {
    bestEl.style.display  = 'none';
  }

  // ── Footer: eval ──
  const evalEl = document.getElementById('analysis-eval');
  if (move.eval_after !== null && move.eval_after !== undefined) {
    const p    = (move.eval_after / 100).toFixed(1);
    const sign = move.eval_after >= 0 ? '+' : '';
    evalEl.textContent = `Position: ${sign}${p} pawns`;
  } else {
    evalEl.textContent = '';
  }
}

function clearExplanation() {
  document.getElementById('analysis-move-label').textContent  = 'Select a move';
  document.getElementById('classification-badge').className   = 'badge';
  document.getElementById('classification-badge').textContent = '';
  const c = document.getElementById('analysis-comment');
  c.textContent = 'Navigate moves with ← → arrows, or click any move in the list.';
  c.classList.add('muted');
  c.style.opacity = '1';
  document.getElementById('analysis-best-move').style.display = 'none';
  document.getElementById('analysis-eval').textContent        = '';
}

// ---------------------------------------------------------------------------
// Coach panel (persistent in left sidebar)
// ---------------------------------------------------------------------------

let coachCollapsed = false;

function toggleCoach() {
  coachCollapsed = !coachCollapsed;
  document.getElementById('coach-panel').classList.toggle('collapsed', coachCollapsed);
  document.getElementById('coach-toggle-icon').textContent = coachCollapsed ? '▼' : '▲';
}

async function loadCoach() {
  const res  = await fetch('/api/patterns');
  const data = await res.json();
  const el   = document.getElementById('coach-content');

  if (data.report) {
    el.textContent = data.report;
    el.classList.remove('muted');
    if (data.generated_at) {
      const date = new Date(data.generated_at * 1000).toLocaleDateString();
      document.getElementById('coach-title').textContent = `♟ Coach · ${date}`;
    }
  } else {
    el.textContent = 'No report yet. Analyze a few games then click ↻ to generate.';
    el.classList.add('muted');
  }
}

async function refreshCoach() {
  const btn     = document.getElementById('coach-refresh-btn');
  const spinner = document.getElementById('coach-spinner');
  const el      = document.getElementById('coach-content');

  btn.disabled     = true;
  spinner.style.display = 'flex';
  el.style.display = 'none';

  await fetch('/api/patterns/generate', { method: 'POST' });

  const poll = setInterval(async () => {
    const res  = await fetch('/api/patterns');
    const data = await res.json();
    if (data.report) {
      clearInterval(poll);
      btn.disabled          = false;
      spinner.style.display = 'none';
      el.style.display      = '';
      el.textContent        = data.report;
      el.classList.remove('muted');
      if (data.generated_at) {
        const date = new Date(data.generated_at * 1000).toLocaleDateString();
        document.getElementById('coach-title').textContent = `♟ Coach · ${date}`;
      }
    }
  }, 3000);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
// Chat dialog
// ---------------------------------------------------------------------------

let chatHistory  = [];   // [{role, content}] — resets on move navigation
let chatMoveKey  = null; // "gameId:moveIdx" — detect when move changes

function resetChat() {
  chatHistory = [];
  document.getElementById('chat-thread').innerHTML = '';
}

function sendChat() {
  const input = document.getElementById('chat-input');
  const msg   = input.value.trim();
  if (!msg || !currentGame) return;

  const moveIdx = currentMoveIdx - 1;
  const moveKey = `${currentGame.id}:${moveIdx}`;

  // Reset chat thread if the user navigated to a different move
  if (moveKey !== chatMoveKey) {
    resetChat();
    chatMoveKey = moveKey;
  }

  input.value = '';
  appendChatMsg('user', msg);

  const btn = document.getElementById('chat-send');
  btn.disabled = true;

  const body = {
    game_id:  currentGame.id,
    move_idx: moveIdx >= 0 ? moveIdx : null,
    message:  msg,
    history:  chatHistory,
  };

  fetch('/api/chat', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(body),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        appendChatMsg('assistant', `Error: ${data.error}`);
      } else {
        chatHistory.push({role: 'user',      content: msg});
        chatHistory.push({role: 'assistant', content: data.reply});
        appendChatMsg('assistant', data.reply);
      }
    })
    .catch(e => appendChatMsg('assistant', `Error: ${e.message}`))
    .finally(() => { btn.disabled = false; });
}

function appendChatMsg(role, text) {
  const thread = document.getElementById('chat-thread');
  const div    = document.createElement('div');
  div.className    = `chat-msg ${role}`;
  div.textContent  = text;
  thread.appendChild(div);
  thread.scrollTop = thread.scrollHeight;
}

// Allow pressing Enter to send
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('chat-input');
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
  }

  // Draggable resize handle for the coach panel (top border of coach section)
  const coachResize = document.getElementById('coach-resize');
  const coachPanel  = document.getElementById('coach-panel');
  if (coachResize && coachPanel) {
    let cDragging    = false;
    let cStartY      = 0;
    let cStartHeight = 0;

    coachResize.addEventListener('mousedown', e => {
      cDragging    = true;
      cStartY      = e.clientY;
      cStartHeight = coachPanel.getBoundingClientRect().height;
      coachResize.classList.add('dragging');
      document.body.style.cursor     = 'ns-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    document.addEventListener('mousemove', e => {
      if (!cDragging) return;
      // Handle is above the panel: drag up → panel grows, drag down → shrinks
      const newH = Math.max(38, Math.min(520, cStartHeight - (e.clientY - cStartY)));
      coachPanel.classList.remove('collapsed');
      coachPanel.style.height = newH + 'px';
    });

    document.addEventListener('mouseup', () => {
      if (!cDragging) return;
      cDragging = false;
      coachResize.classList.remove('dragging');
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
    });
  }

  // Draggable resize handle for the analysis comment box
  const handle = document.getElementById('analysis-body-resize');
  const body   = document.getElementById('analysis-body');
  if (handle && body) {
    let dragging    = false;
    let startY      = 0;
    let startHeight = 0;

    handle.addEventListener('mousedown', e => {
      dragging    = true;
      startY      = e.clientY;
      startHeight = body.getBoundingClientRect().height;
      handle.classList.add('dragging');
      document.body.style.cursor     = 'ns-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const newH = Math.max(60, Math.min(520, startHeight + (e.clientY - startY)));
      body.style.height = newH + 'px';
    });

    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
    });
  }
});

// ---------------------------------------------------------------------------

// Escape HTML special characters to safely insert user-provided strings into innerHTML.
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Show a brief notification at the bottom of the screen.
function toast(msg) {
  const el = document.createElement('div');
  el.className   = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.classList.add('fade');
    setTimeout(() => el.remove(), 300);
  }, 2800);
}
