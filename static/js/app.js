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
// Stockfish best-move arrow + actual move arrow
// ---------------------------------------------------------------------------

// Convert a square name like "g1" to a board array index (0=a8, 63=h1).
function uciSqToIdx(sq) {
  const file = sq.charCodeAt(0) - 97;   // 'a'=0 … 'h'=7
  const rank = 8 - parseInt(sq[1], 10); // '1'=7, '8'=0
  return rank * 8 + file;
}

// Return {fromIdx, toIdx} for the piece that moved between two FEN positions.
// Handles the multi-square cases: castling (two pieces move — show the king's
// move) and en passant (three squares change).
function computeActualMove(fenBefore, fenAfter) {
  const before = fenToArray(fenBefore);
  const after  = fenToArray(fenAfter);
  const vacated = [];   // squares a piece left
  const arrived = [];   // squares a piece appeared on or changed
  for (let i = 0; i < 64; i++) {
    if (before[i] === after[i]) continue;
    if (after[i] === null) vacated.push(i);
    else arrived.push(i);
  }
  if (!vacated.length || !arrived.length) return null;

  // Castling: the king and a rook both move — prefer the king's from/to.
  if (vacated.length === 2 && arrived.length === 2) {
    const kingFrom = vacated.find(i => before[i] === 'K' || before[i] === 'k');
    const kingTo   = arrived.find(i => after[i]  === 'K' || after[i]  === 'k');
    if (kingFrom !== undefined && kingTo !== undefined) {
      return { fromIdx: kingFrom, toIdx: kingTo };
    }
  }

  // Normal move / capture / en passant: match the arriving piece to the square
  // that piece left (promotion won't match letters, so fall back to first pair).
  const toIdx   = arrived[0];
  const fromIdx = vacated.find(i => before[i] === after[toIdx]) ?? vacated[0];
  return { fromIdx, toIdx };
}

// Draw move arrows on a single SVG overlay.
//
// Actual-move arrow colour matches the move-list button colour:
//   • Blue   — played move IS the engine's top choice (isMatch)
//   • Red    — blunder (??)
//   • Orange — mistake (?)
//   • Yellow — inaccuracy
//   • Grey   — opponent move or unknown classification
//
// The engine-suggestion arrow is always green when it differs from what was played.
//
// classification: the move's Stockfish classification string, or null for opponent moves.
function drawMoveArrows(suggestedUci, actualMove, orientation, classification) {
  const existing = document.getElementById('best-move-svg');
  if (existing) existing.remove();

  const hasSuggested = suggestedUci && suggestedUci.length >= 4;
  const hasActual    = actualMove && actualMove.fromIdx >= 0 && actualMove.toIdx >= 0;
  if (!hasSuggested && !hasActual) return;

  // Detect whether the played move is the engine's top choice.
  // If so, we collapse both arrows into one blue arrow instead of grey + green.
  let isMatch = false;
  if (hasSuggested && hasActual) {
    const sfFrom = uciSqToIdx(suggestedUci.slice(0, 2));
    const sfTo   = uciSqToIdx(suggestedUci.slice(2, 4));
    isMatch = sfFrom === actualMove.fromIdx && sfTo === actualMove.toIdx;
  }

  const NS  = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.id = 'best-move-svg';
  svg.setAttribute('viewBox', '0 0 800 800');
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.style.cssText =
    'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10';

  const defs = document.createElementNS(NS, 'defs');
  function makeMarker(id, fillColor) {
    const m = document.createElementNS(NS, 'marker');
    m.setAttribute('id', id);
    m.setAttribute('markerWidth', '5');
    m.setAttribute('markerHeight', '5');
    m.setAttribute('refX', '4.5');
    m.setAttribute('refY', '2.5');
    m.setAttribute('orient', 'auto');
    const p = document.createElementNS(NS, 'polygon');
    p.setAttribute('points', '0 0, 5 2.5, 0 5');
    p.setAttribute('fill', fillColor);
    m.appendChild(p);
    defs.appendChild(m);
  }

  // Pick the actual-move arrow colour based on classification.
  // isMatch (blue) takes priority — if you played the engine's top move it's always blue.
  const ACTUAL_COLORS = {
    blunder:    ['rgba(239,68,68,0.90)',  'rgba(239,68,68,0.35)'],   // red
    mistake:    ['rgba(249,115,22,0.90)', 'rgba(249,115,22,0.35)'],  // orange
    inaccuracy: ['rgba(250,204,21,0.90)', 'rgba(250,204,21,0.30)'],  // yellow
    _blue:      ['rgba(96,165,250,0.90)', 'rgba(96,165,250,0.40)'],  // blue (isMatch)
    _grey:      ['rgba(200,200,200,0.65)','rgba(200,200,200,0.30)'], // grey (opponent/unknown)
  };

  const actualKey   = isMatch ? '_blue' : (ACTUAL_COLORS[classification] ? classification : '_grey');
  const [actualStroke, actualFill] = ACTUAL_COLORS[actualKey];
  const actualHeadColor = actualStroke.replace(/[\d.]+\)$/, '0.95)'); // fully opaque for arrowhead

  if (isMatch) {
    makeMarker('actual-head', actualHeadColor);
  } else {
    if (hasActual)    makeMarker('actual-head', actualHeadColor);
    if (hasSuggested) makeMarker('sf-head', 'rgba(34,197,94,0.92)');  // green suggestion
  }
  svg.appendChild(defs);

  function addArrow(fromIdx, toIdx, strokeColor, circColor, markerId) {
    const fromVis = orientation === 'black' ? 63 - fromIdx : fromIdx;
    const toVis   = orientation === 'black' ? 63 - toIdx   : toIdx;
    const x1 = (fromVis % 8) * 100 + 50, y1 = Math.floor(fromVis / 8) * 100 + 50;
    const x2 = (toVis   % 8) * 100 + 50, y2 = Math.floor(toVis   / 8) * 100 + 50;
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) return;
    const scale = Math.max(0, (len - 30)) / len;

    const circ = document.createElementNS(NS, 'circle');
    circ.setAttribute('cx', x1); circ.setAttribute('cy', y1);
    circ.setAttribute('r', '20'); circ.setAttribute('fill', circColor);
    svg.appendChild(circ);

    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', x1); line.setAttribute('y1', y1);
    line.setAttribute('x2', x1 + dx * scale); line.setAttribute('y2', y1 + dy * scale);
    line.setAttribute('stroke', strokeColor);
    line.setAttribute('stroke-width', '16');
    line.setAttribute('stroke-linecap', 'round');
    line.setAttribute('marker-end', `url(#${markerId})`);
    svg.appendChild(line);
  }

  // Actual-move arrow (always drawn when we have FEN data)
  if (hasActual) {
    addArrow(actualMove.fromIdx, actualMove.toIdx, actualStroke, actualFill, 'actual-head');
  }
  // Green engine suggestion on top (only when it differs from actual)
  if (hasSuggested && !isMatch) {
    addArrow(uciSqToIdx(suggestedUci.slice(0, 2)), uciSqToIdx(suggestedUci.slice(2, 4)),
      'rgba(34,197,94,0.85)', 'rgba(34,197,94,0.40)', 'sf-head');
  }

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

  order.forEach((sqIdx, vi) => {
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

    // Coordinate labels along the left edge (ranks) and bottom edge (files),
    // based on the VISUAL position (vi) so they follow board orientation.
    const visCol = vi % 8, visRow = Math.floor(vi / 8);
    if (visCol === 0) {
      const lbl = document.createElement('span');
      lbl.className   = 'coord coord-rank';
      lbl.textContent = 8 - rank;
      sq.appendChild(lbl);
    }
    if (visRow === 7) {
      const lbl = document.createElement('span');
      lbl.className   = 'coord coord-file';
      lbl.textContent = 'abcdefgh'[file];
      sq.appendChild(lbl);
    }

    const piece = pieces[sqIdx];
    if (piece) {
      sq.appendChild(pieceImg(piece));
    }

    el.appendChild(sq);
  });
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
  syncMobileViewportHeight();
  window.addEventListener('resize', () => { syncMobileViewportHeight(); ensureMobileTab(); });
  window.addEventListener('orientationchange', syncMobileViewportHeight);

  // Initialize mobile tab layout — starts on Games tab so user picks a game first
  if (isMobileLayout()) setMobileTab('games');

  // Swipe left/right on the board to navigate moves (mobile)
  const boardContainer = document.getElementById('board-container');
  if (boardContainer) {
    let swipeStartX = 0;
    let swipeStartY = 0;
    boardContainer.addEventListener('touchstart', e => {
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
    }, { passive: true });
    boardContainer.addEventListener('touchend', e => {
      if (!currentGame || !currentGame.moves) return;
      const dx = e.changedTouches[0].clientX - swipeStartX;
      const dy = e.changedTouches[0].clientY - swipeStartY;
      // Only respond to swipes that are more horizontal than vertical, and >35px
      if (Math.abs(dx) > 35 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        if (dx < 0) stepMove(1);   // swipe left → next move
        else        stepMove(-1);  // swipe right → prev move
      }
    }, { passive: true });
  }

  renderBoard(STARTING_FEN, 'white', null);
  loadCoachModel();
  loadGameList();
  loadCoach();

  // Wire up eval graph: click to seek, hover for a move/eval tooltip
  const canvas = document.getElementById('eval-graph');
  if (canvas) {
    const moveIdxAtEvent = e => {
      const rect = canvas.getBoundingClientRect();
      const x    = e.clientX - rect.left;
      const n    = currentGame.moves.length;
      return Math.min(n - 1, Math.max(0, Math.floor(x / (rect.width / n))));
    };

    canvas.addEventListener('click', e => {
      if (!currentGame || !currentGame.moves) return;
      goToMove(moveIdxAtEvent(e) + 1);
    });

    const tip = document.createElement('div');
    tip.id = 'eval-tooltip';
    document.body.appendChild(tip);

    canvas.addEventListener('mousemove', e => {
      if (!currentGame || !currentGame.moves) { tip.style.display = 'none'; return; }
      const idx = moveIdxAtEvent(e);
      const m   = currentGame.moves[idx];
      if (!m) { tip.style.display = 'none'; return; }

      const dot  = m.color === 'black' ? '…' : '.';
      let evalTxt = '';
      if (m.eval_after !== null && m.eval_after !== undefined) {
        // eval_after is mover-POV — normalise to white's POV for a stable axis
        const cp = m.color === 'black' ? -m.eval_after : m.eval_after;
        evalTxt  = ` · ${cp >= 0 ? '+' : ''}${(cp / 100).toFixed(1)}`;
      }
      const cls = (m.color === currentGame.played_as &&
                   ['inaccuracy', 'mistake', 'blunder'].includes(m.classification))
        ? ` · ${m.classification}` : '';

      tip.textContent    = `${m.move_number}${dot} ${m.san}${evalTxt}${cls}`;
      tip.style.display  = 'block';
      const rect = canvas.getBoundingClientRect();
      const tw   = tip.offsetWidth;
      tip.style.left = Math.min(window.innerWidth - tw - 8, Math.max(8, e.clientX - tw / 2)) + 'px';
      tip.style.top  = (rect.top - tip.offsetHeight - 6) + 'px';
    });

    canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  }

  document.addEventListener('keydown', e => {
    // Don't hijack keys while the user is typing (e.g. in the chat input)
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (!currentGame || !currentGame.moves) return;
    const actions = {
      'ArrowLeft':  () => stepMove(-1),
      'ArrowRight': () => stepMove(1),
      'ArrowUp':    () => goToMove(0),
      'ArrowDown':  () => goToLastMove(),
      '[':          () => jumpToMistake(-1),
      ']':          () => jumpToMistake(1),
      'f':          () => flipBoard(),
    };
    const action = actions[e.key];
    if (action) {
      e.preventDefault();   // arrow keys would otherwise also scroll the page
      action();
    }
  });
});


// ---------------------------------------------------------------------------
// Game list
// ---------------------------------------------------------------------------

async function loadCoachModel() {
  try {
    const res = await fetch('/api/model');
    const data = await res.json();
    updateModelSwitch(data.mode);
  } catch (e) {
    console.warn('Model settings unavailable:', e);
  }
}

function updateModelSwitch(mode) {
  document.querySelectorAll('#model-switch button').forEach(btn => {
    btn.classList.remove('active');
    btn.disabled = false;
  });

  const active = document.getElementById(`model-${mode}`);
  if (active) active.classList.add('active');
}

async function setCoachModel(mode) {
  const buttons = document.querySelectorAll('#model-switch button');
  buttons.forEach(btn => { btn.disabled = true; });

  try {
    const res = await fetch('/api/model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode}),
    });
    const data = await res.json();
    if (data.error) {
      toast(data.error);
      return;
    }

    updateModelSwitch(data.mode);
    const label = data.mode === 'opus' ? 'Opus' : 'Sonnet';
    toast(`${label} selected for new analyses`);
  } catch (e) {
    toast('Model switch failed: ' + e.message);
  } finally {
    buttons.forEach(btn => { btn.disabled = false; });
  }
}

async function loadGameList() {
  try {
    const res   = await fetch('/api/games');
    const games = await res.json();
    renderGameList(games);
  } catch (e) {
    // The PWA shell loads from the service-worker cache even when the server
    // is down — without this, the app looks alive but nothing works.
    console.warn('Game list unavailable:', e);
    document.getElementById('game-list').innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">⚠︎</div>
        <p>Server not reachable</p>
        <p class="empty-state-hint">Start the Chess Analyzer server, then
          <a href="#" onclick="location.reload(); return false">reload</a>.</p>
      </div>`;
  }
}

// Compact human date: "Today", "Yesterday", "Mon", or "Jun 12" / "Jun 12 '25".
function relativeDate(epochSeconds) {
  if (!epochSeconds) return '';
  const d   = new Date(epochSeconds * 1000);
  const now = new Date();
  const startOfDay = dt => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7)   return d.toLocaleDateString(undefined, { weekday: 'short' });
  const opts = { month: 'short', day: 'numeric' };
  if (d.getFullYear() !== now.getFullYear()) opts.year = '2-digit';
  return d.toLocaleDateString(undefined, opts);
}

function renderGameList(games) {
  const el = document.getElementById('game-list');
  if (!games.length) {
    el.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">♞</div>
        <p>No games yet</p>
        <p class="empty-state-hint">Press <strong>↓ Fetch</strong> to pull your recent games from chess.com</p>
      </div>`;
    return;
  }

  el.innerHTML = games.map(g => {
    const date     = relativeDate(g.end_time);
    const analyzed = g.analyzed ? '<span class="analyzed-dot" title="Analyzed"></span>' : '';
    const opening  = g.opening  ? `<div class="game-opening">${escHtml(g.opening)}</div>` : '';
    const actionLabel = g.analyzed ? '↺' : 'Analyze';
    const actionTitle = g.analyzed ? 'Re-analyze this game' : 'Analyze this game';
    return `
      <div class="game-item" onclick="loadGame('${g.id}')" data-id="${g.id}">
        <div class="game-item-header">
          <span class="result-badge result-${g.result}">${g.result.toUpperCase()}</span>
          <span class="game-opponent">${escHtml(g.opponent)}</span>
          ${analyzed}
          <button class="game-analyze-btn" data-analyzed="${g.analyzed ? 1 : 0}"
            onclick="analyzeGameFromList(event, '${g.id}')" title="${actionTitle}">
            ${actionLabel}
          </button>
        </div>
        <div class="game-meta">${g.time_class} · ${g.played_as} · ${date}</div>
        ${opening}
      </div>`;
  }).join('');
}

function syncMobileViewportHeight() {
  document.documentElement.style.setProperty('--app-height', `${window.innerHeight}px`);
}

function isMobileLayout() {
  return window.matchMedia('(max-width: 900px)').matches;
}

// When the window crosses from desktop into the mobile layout mid-session,
// no panel has .mobile-active yet and everything would be hidden — pick a
// sensible tab so the UI never goes blank.
function ensureMobileTab() {
  if (!isMobileLayout()) return;
  const anyActive = document.querySelector(
    '#game-list-panel.mobile-active, #board-panel.mobile-active, #analysis-panel.mobile-active');
  if (!anyActive) setMobileTab(currentGame ? 'board' : 'games');
}

// ---------------------------------------------------------------------------
// Mobile tab navigation
// ---------------------------------------------------------------------------

let _currentMobileTab = 'games';

// Switch to a named tab ('games', 'board', 'analysis') on mobile.
// Also redraws the eval graph when returning to the board tab so it
// picks up the correct canvas dimensions.
function setMobileTab(tab) {
  if (!isMobileLayout()) return;

  _currentMobileTab = tab;

  // Update tab button highlight
  ['games', 'board', 'analysis'].forEach(t => {
    const btn = document.getElementById(`tab-btn-${t}`);
    if (btn) btn.classList.toggle('active', t === tab);
  });

  // Show only the active panel
  const panelIds = { games: 'game-list-panel', board: 'board-panel', analysis: 'analysis-panel' };
  Object.entries(panelIds).forEach(([t, id]) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('mobile-active', t === tab);
  });

  // Redraw eval graph — canvas dimensions change when the board panel becomes visible
  if (tab === 'board' && currentGame && currentGame.moves) {
    requestAnimationFrame(() => {
      drawEvalGraph(currentGame.moves, currentGame.played_as, currentMoveIdx);
    });
  }
}

// ---------------------------------------------------------------------------
// Nav overflow menu (bulk actions)
// ---------------------------------------------------------------------------

function toggleNavMenu(force) {
  const menu = document.getElementById('nav-menu');
  const btn  = document.getElementById('btn-nav-menu');
  if (!menu) return;
  const open = force !== undefined ? force : !menu.classList.contains('open');
  menu.classList.toggle('open', open);
  if (btn) btn.setAttribute('aria-expanded', String(open));
}

// Close the menu when clicking anywhere outside it
document.addEventListener('click', e => {
  const wrap = document.getElementById('nav-menu-wrap');
  if (wrap && !wrap.contains(e.target)) toggleNavMenu(false);
});

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
  if (isMobileLayout()) setMobileTab('board');

  stopPolling();

  // Show analyzing banner immediately while we wait for the fetch — avoid
  // flashing an empty game-view with no moves during the round trip.
  document.getElementById('placeholder').style.display      = 'none';
  document.getElementById('game-view').style.display        = 'none';
  document.getElementById('analyzing-banner').style.display = 'flex';
  document.getElementById('move-list').innerHTML            = '';
  clearExplanation();

  let game;
  try {
    const res = await fetch(`/api/game/${gameId}`);
    game = await res.json();
  } catch (e) {
    document.getElementById('analyzing-banner').style.display = 'none';
    document.getElementById('placeholder').style.display      = '';
    toast('Could not load game — is the server running?');
    return;
  }
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
  try {
    const res  = await fetch(`/api/game/${gameId}/status`);
    const data = await res.json();
    if (!data.analyzed) return;
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
  } catch (e) {
    // transient network error (e.g. server reloading) — keep polling
    console.warn('Status poll failed:', e);
  }
}

async function checkCommentaryStatus(gameId) {
  // Poll until Claude commentary is ready — or until the server gives up
  // (commentary_pending goes false with claude_ok still 0).
  try {
    const res  = await fetch(`/api/game/${gameId}`);
    const game = await res.json();
    if (game.commentary_pending) return;
    stopPolling();
    currentGame = game;
    if (game.claude_ok) {
      playChime();
      updateCommentary(game.moves);
    } else {
      // Server exhausted its retries — show the failed state with a retry button
      document.getElementById('commentary-banner').style.display = 'none';
      document.getElementById('analysis-error').style.display    = 'flex';
    }
  } catch (e) {
    console.warn('Commentary poll failed:', e);
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
  // Update game overview now that the summary has arrived
  renderGameOverview(currentGame);
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
  document.getElementById('btn-redo-commentary').style.display = '';

  // Commentary loading banner: shown while Claude is still generating
  const commentaryBanner = document.getElementById('commentary-banner');
  if (commentaryBanner) {
    commentaryBanner.style.display = game.commentary_pending ? 'flex' : 'none';
  }

  // Warning if Claude commentary failed and the server has stopped retrying.
  // (Fast Stockfish comments fill every move, so "any explanation exists" can't
  // detect failure — claude_ok is the real signal.)
  const commentaryFailed = game.claude_ok === false && !game.commentary_pending;
  document.getElementById('analysis-error').style.display = commentaryFailed ? 'flex' : 'none';

  // Game overview
  renderGameOverview(game);

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
  }
  let actualMove     = null;
  let classification = null;
  if (currentGame && currentGame.moves && currentMoveIdx > 0) {
    const moves = currentGame.moves;
    const m = moves[currentMoveIdx - 1];
    bmUci = m ? (m.best_move_uci || null) : null;
    if (m && m.fen_before && m.fen) {
      actualMove = computeActualMove(m.fen_before, m.fen);
    }
    if (m && currentGame && m.color === currentGame.played_as) {
      classification = m.classification || null;
    }
  }
  renderBoard(fen, boardOrientation, hl);
  drawMoveArrows(bmUci, actualMove, boardOrientation, classification);
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
        html += `<div class="move-pair"><span class="move-num">${m.move_number}…</span>`;
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

  // Draw move arrows: actual move played + engine suggestion (if any).
  // classification is passed only for the player's own moves so the arrow colour
  // matches the move-list button (red=blunder, orange=mistake, yellow=inaccuracy,
  // blue=best, grey=opponent/unknown).
  let bmUci          = null;
  let actualMove     = null;
  let classification = null;
  if (target > 0 && moves[target - 1]) {
    const m = moves[target - 1];
    bmUci = m.best_move_uci || null;
    if (m.fen_before && m.fen) {
      actualMove = computeActualMove(m.fen_before, m.fen);
    }
    // Only apply classification colour for the player's own moves
    if (currentGame && m.color === currentGame.played_as) {
      classification = m.classification || null;
    }
  }
  drawMoveArrows(bmUci, actualMove, boardOrientation, classification);

  updateMoveHighlight(target - 1);
  updateMoveCounter();
  drawEvalGraph(moves, currentGame.played_as, target);  // redraw cursor

  if (target > 0) {
    showExplanation(moves[target - 1], currentGame.played_as);
    scrollMoveIntoView(target - 1);
  } else {
    clearExplanation();
  }

  // Switch chat to this move's conversation (restores prior messages if any)
  switchChatToMove(`${currentGame.id}:${target - 1}`);
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
  if (currentGame.moves && !confirm('Re-analyze this game from scratch? This re-runs Stockfish and Claude.')) return;
  const btn = document.getElementById('btn-reanalyze');
  btn.disabled = true;
  btn.textContent = '…';

  try {
    await fetch(`/api/game/${currentGame.id}/analyze`, { method: 'POST' });
    document.getElementById('analyzing-banner').style.display = 'flex';
    document.getElementById('game-view').style.display        = 'none';
    document.getElementById('analysis-error').style.display   = 'none';
    stopPolling();
    pollTimer = setInterval(() => checkAnalysisStatus(currentGame.id), 4000);
  } catch (e) {
    toast('Re-analysis failed: ' + e.message);
  } finally {
    btn.disabled    = false;
    btn.textContent = '↺ Re-analyze';
  }
}

async function analyzeGameFromList(event, gameId) {
  event.stopPropagation();
  const btn = event.currentTarget;
  // Re-analyzing an already analyzed game wipes it and re-runs the whole
  // (slow, paid) pipeline — make sure it wasn't a stray tap.
  const isReanalysis = btn.dataset.analyzed === '1';
  if (isReanalysis && !confirm('Re-analyze this game from scratch? This re-runs Stockfish and Claude.')) return;
  btn.disabled = true;
  btn.textContent = '…';

  try {
    await fetch(`/api/game/${gameId}/analyze`, { method: 'POST' });
    toast('Analyzing this game…');
    await loadGame(gameId);
  } catch (e) {
    toast('Could not start analysis: ' + e.message);
    btn.disabled = false;
  }
}

async function reAnalyzeAll() {
  const btn = document.getElementById('btn-reanalyze-all');
  btn.disabled = true;

  try {
    // Fetch the full game list
    const res   = await fetch('/api/games');
    const games = await res.json();
    const analyzed = games.filter(g => g.analyzed);
    if (!analyzed.length) {
      toast(games.length ? 'No analyzed games yet.' : 'No games to re-analyze. Fetch games first.');
      return;
    }

    if (!confirm(`Re-analyze all ${analyzed.length} games from scratch? This re-runs Stockfish and Claude on every game and takes a while.`)) return;

    toast(`Queuing ${analyzed.length} games for re-analysis…`);

    // Fire-and-forget each game — the server processes them in background threads
    for (const g of analyzed) {
      await fetch(`/api/game/${g.id}/analyze`, { method: 'POST' });
    }

    toast(`${analyzed.length} games queued. Analysis runs in the background.`);
    loadGameList();
  } catch (e) {
    toast('Re-analyze all failed: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function redoCommentary() {
  if (!currentGame) return;
  const btn = document.getElementById('btn-redo-commentary');
  btn.disabled = true;
  try {
    await fetch(`/api/game/${currentGame.id}/redo-commentary`, { method: 'POST' });
    document.getElementById('commentary-banner').style.display = 'flex';
    document.getElementById('analysis-error').style.display    = 'none';
    stopPolling();
    // Commentary keeps the Stockfish data — poll the commentary flag, not the
    // analyzed flag (which is already 1 and would fire instantly).
    pollTimer = setInterval(() => checkCommentaryStatus(currentGame.id), 5000);
  } catch (e) {
    toast('Could not restart commentary: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function refreshAllCommentary() {
  const btn = document.getElementById('btn-refresh-commentary');
  if (!confirm('Re-run Claude commentary on every analyzed game?')) return;
  btn.disabled = true;
  try {
    const res  = await fetch('/api/commentary/refresh-all', { method: 'POST' });
    const data = await res.json();
    if (data.error) { toast(data.error); return; }
    toast(`Refreshing commentary for ${data.count} games in the background.`);
  } catch (e) {
    toast('Refresh failed: ' + e.message);
  } finally {
    btn.disabled = false;
  }
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
  ctx.fillStyle = '#0e1120';
  ctx.fillRect(0, 0, w, h);

  // Center line
  ctx.strokeStyle = '#262b4a';
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

  // Green fill: winning region (clip to above midY), fading toward the center line
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, midY);
  ctx.clip();
  buildPath();
  const gGrad = ctx.createLinearGradient(0, 0, 0, midY);
  gGrad.addColorStop(0, 'rgba(34,197,94,0.42)');
  gGrad.addColorStop(1, 'rgba(34,197,94,0.10)');
  ctx.fillStyle = gGrad;
  ctx.fill();
  ctx.restore();

  // Red fill: losing region (clip to below midY), fading away from the center line
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, midY, w, midY);
  ctx.clip();
  buildPath();
  const rGrad = ctx.createLinearGradient(0, midY, 0, h);
  rGrad.addColorStop(0, 'rgba(239,68,68,0.10)');
  rGrad.addColorStop(1, 'rgba(239,68,68,0.42)');
  ctx.fillStyle = rGrad;
  ctx.fill();
  ctx.restore();

  // Eval line
  ctx.beginPath();
  ctx.moveTo(0.5 * xStep, evalToY(evals[0]));
  for (let i = 1; i < n; i++) ctx.lineTo((i + 0.5) * xStep, evalToY(evals[i]));
  ctx.strokeStyle = 'rgba(230,234,244,0.45)';
  ctx.lineWidth   = 1.25;
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
    ctx.strokeStyle = '#8b5cf6';
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, h);
    ctx.stroke();
    // Dot marking the eval at the current move
    ctx.beginPath();
    ctx.arc(cx, evalToY(evals[currentIdx - 1]), 3, 0, Math.PI * 2);
    ctx.fillStyle = '#c4b5fd';
    ctx.fill();
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

// Jump to the player's previous/next inaccuracy, mistake, or blunder.
function jumpToMistake(dir) {
  if (!currentGame || !currentGame.moves) return;
  const moves = currentGame.moves;
  const isErr = m => m.color === currentGame.played_as &&
    ['inaccuracy', 'mistake', 'blunder'].includes(m.classification);
  let i = currentMoveIdx - 1 + dir;
  while (i >= 0 && i < moves.length) {
    if (isErr(moves[i])) { goToMove(i + 1); return; }
    i += dir;
  }
  toast(dir > 0 ? 'No mistakes after this move' : 'No mistakes before this move');
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
      commentEl.textContent = `Opponent's move — coaching notes appear on your own moves.`;
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
  renderGameOverview(currentGame);
}

// ---------------------------------------------------------------------------
// Game Overview section
// ---------------------------------------------------------------------------

let gameOverviewCollapsed = false;

function toggleGameOverview() {
  gameOverviewCollapsed = !gameOverviewCollapsed;
  document.getElementById('game-overview-section').classList.toggle('collapsed', gameOverviewCollapsed);
}

// Per-game accuracy from the Stockfish data already stored on each move,
// using the lichess method: convert evals to win-percentage, score each move
// by how much win-chance it gave up, then average. Working in win% (not raw
// centipawns) keeps mate-score swings (±2000cp+) from flattening the result.
function winPercent(cp) {
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);
}

function computeGameStats(moves, playedAs) {
  const mine = (moves || []).filter(m => m.color === playedAs);
  let counts = { blunder: 0, mistake: 0, inaccuracy: 0 };
  let totalLoss = 0, accSum = 0, evaluated = 0;
  mine.forEach(m => {
    if (counts[m.classification] !== undefined) counts[m.classification]++;
    if (m.eval_before === null || m.eval_before === undefined ||
        m.eval_after  === null || m.eval_after  === undefined) return;
    // ACPL: cap each move's loss at 1000cp so mate scores don't distort it
    totalLoss += Math.min(1000, Math.max(0, m.delta ?? 0));
    const winDrop = Math.max(0, winPercent(m.eval_before) - winPercent(m.eval_after));
    accSum += Math.max(0, Math.min(100, 103.1668 * Math.exp(-0.04354 * winDrop) - 3.1669));
    evaluated++;
  });
  if (!evaluated) return null;
  return {
    acpl: Math.round(totalLoss / evaluated),
    accuracy: Math.round(accSum / evaluated),
    ...counts,
  };
}

function renderGameStats(game) {
  const el = document.getElementById('game-stats-row');
  if (!el) return;
  const stats = game && game.moves ? computeGameStats(game.moves, game.played_as) : null;
  if (!stats) {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  el.innerHTML = `
    <span class="stat-chip stat-accuracy" title="Estimated accuracy from average centipawn loss">Accuracy ${stats.accuracy}%</span>
    <span class="stat-chip" title="Average centipawn loss per move">Avg loss ${stats.acpl}cp</span>
    <span class="stat-chip stat-blunder" title="Blunders">${stats.blunder} ??</span>
    <span class="stat-chip stat-mistake" title="Mistakes">${stats.mistake} ?</span>
    <span class="stat-chip stat-inaccuracy" title="Inaccuracies">${stats.inaccuracy} ?!</span>`;
}

function renderGameOverview(game) {
  const section = document.getElementById('game-overview-section');
  const textEl  = document.getElementById('game-overview-text');

  if (!game || !game.moves) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';
  renderGameStats(game);
  if (game.game_summary) {
    textEl.textContent = game.game_summary;
    textEl.classList.remove('muted');
  } else if (game.commentary_pending) {
    textEl.textContent = '⏳ Generating overview…';
    textEl.classList.add('muted');
  } else {
    textEl.textContent = 'No overview available for this game.';
    textEl.classList.add('muted');
  }
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
  const el = document.getElementById('coach-content');

  // Prefer the Claude-generated patterns report (deep personal analysis)
  try {
    const res  = await fetch('/api/patterns');
    const data = await res.json();
    if (data.report) {
      renderPatterns(data.report, data.generated_at);
      return;
    }
  } catch (e) {
    console.warn('Patterns unavailable:', e);
  }

  // Fall back to rule-based blueprint if no Claude report yet
  try {
    const blueprintRes = await fetch('/api/blueprint');
    const blueprint = await blueprintRes.json();
    if (!blueprint.error) {
      renderBlueprint(blueprint);
      return;
    }
  } catch (e) {
    console.warn('Blueprint unavailable:', e);
  }

  el.textContent = 'No report yet. Analyze a few games then click ↻ to generate.';
  el.classList.add('muted');
}

function renderPatterns(report, generated_at) {
  const el = document.getElementById('coach-content');
  el.classList.remove('muted');

  if (generated_at) {
    const date = new Date(generated_at * 1000).toLocaleDateString();
    document.getElementById('coach-title').textContent = `♟ Coach · ${date}`;
  }

  // Prepend a "cross-game analysis" label so it's clear this isn't about the current game
  const disclaimer = '<div class="coach-scope-note">Analysis across all your analyzed games — not the current game</div>';

  // Render bullet points and bold text from the Claude report
  const html = disclaimer + report
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .split(/\n+/)
    .map(line => {
      line = line.trim();
      if (!line) return '';
      if (line.startsWith('•')) {
        return `<div class="pattern-bullet">${line.slice(1).trim()}</div>`;
      }
      return `<p>${line}</p>`;
    })
    .join('');

  el.innerHTML = html;
}

function renderBlueprint(blueprint) {
  const el = document.getElementById('coach-content');
  el.classList.remove('muted');

  if (!blueprint.ready) {
    el.textContent = blueprint.summary;
    el.classList.add('muted');
    return;
  }

  const metrics = (blueprint.metrics || []).map(m => `
    <div class="blueprint-metric">
      <span>${escHtml(m.label)}</span>
      <strong>${escHtml(m.value)}</strong>
    </div>`).join('');

  const focusAreas = (blueprint.focus_areas || []).map(area => `
    <section class="blueprint-focus">
      <h3>${escHtml(area.title)}</h3>
      <p>${escHtml(area.why)}</p>
      <div class="blueprint-practice">${escHtml(area.practice)}</div>
    </section>`).join('');

  const nextSteps = (blueprint.next_steps || []).map(step =>
    `<li>${escHtml(step)}</li>`
  ).join('');

  el.innerHTML = `
    <div class="blueprint">
      <p class="blueprint-summary">${escHtml(blueprint.summary)}</p>
      <div class="blueprint-metrics">${metrics}</div>
      ${focusAreas}
      ${nextSteps ? `
        <section class="blueprint-next">
          <h3>Next practice plan</h3>
          <ol>${nextSteps}</ol>
        </section>` : ''}
    </div>`;
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
      await loadCoach();
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

let chatMoveKey = null; // "gameId:moveIdx" — current move key

const CHAT_STORAGE_KEY = 'chess-chat-v1';

function _loadChatStore() {
  try { return JSON.parse(localStorage.getItem(CHAT_STORAGE_KEY) || '{}'); }
  catch(e) { return {}; }
}

function _saveChatStore(store) {
  try { localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(store)); }
  catch(e) { /* storage full or unavailable */ }
}

function currentChatHistory() {
  return _loadChatStore()[chatMoveKey] || [];
}

function _appendToHistory(moveKey, userMsg, assistantMsg) {
  const store = _loadChatStore();
  if (!store[moveKey]) store[moveKey] = [];
  store[moveKey].push({role: 'user',      content: userMsg});
  store[moveKey].push({role: 'assistant', content: assistantMsg});
  _saveChatStore(store);
}

function resetChat() {
  chatMoveKey = null;
  document.getElementById('chat-thread').innerHTML = '';
}

function switchChatToMove(newKey) {
  if (newKey === chatMoveKey) return;
  chatMoveKey = newKey;

  // Restore persisted conversation for this move
  const thread = document.getElementById('chat-thread');
  thread.innerHTML = '';
  const history = _loadChatStore()[newKey] || [];
  history.forEach(m => appendChatMsg(m.role, m.content));
}

function sendChat() {
  const input = document.getElementById('chat-input');
  const msg   = input.value.trim();
  if (!msg || !currentGame) return;

  const moveIdx = currentMoveIdx - 1;
  switchChatToMove(`${currentGame.id}:${moveIdx}`);

  input.value = '';
  appendChatMsg('user', msg);

  const btn = document.getElementById('chat-send');
  btn.disabled = true;

  const history = currentChatHistory();
  const body = {
    game_id:  currentGame.id,
    move_idx: moveIdx >= 0 ? moveIdx : null,
    message:  msg,
    history:  history,
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
        _appendToHistory(chatMoveKey, msg, data.reply);
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

  // ── Column resize handles (left / right sidebars) ──────────
  function makeColResizer(handleId, panelId, side, minW, maxW, storageKey) {
    const rHandle = document.getElementById(handleId);
    const panel   = document.getElementById(panelId);
    if (!rHandle || !panel) return;

    // Restore saved width
    const saved = localStorage.getItem(storageKey);
    if (saved) panel.style.width = saved + 'px';

    let colDragging = false;
    let colStartX   = 0;
    let colStartW   = 0;

    rHandle.addEventListener('mousedown', e => {
      colDragging = true;
      colStartX   = e.clientX;
      colStartW   = panel.getBoundingClientRect().width;
      rHandle.classList.add('dragging');
      document.body.style.cursor     = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    document.addEventListener('mousemove', e => {
      if (!colDragging) return;
      const delta = side === 'left'
        ? e.clientX - colStartX    // drag right → panel grows
        : colStartX - e.clientX;   // drag left  → panel grows
      const newW = Math.max(minW, Math.min(maxW, colStartW + delta));
      panel.style.width = newW + 'px';
    });

    document.addEventListener('mouseup', () => {
      if (!colDragging) return;
      colDragging = false;
      rHandle.classList.remove('dragging');
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
      localStorage.setItem(storageKey, parseInt(panel.style.width));
    });
  }

  makeColResizer('resize-handle-left',  'game-list-panel', 'left',  160, 480, 'chess-left-w');
  makeColResizer('resize-handle-right', 'analysis-panel',  'right', 300, 700, 'chess-right-w');
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
