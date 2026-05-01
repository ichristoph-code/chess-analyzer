import json
import os
import re
import time
from datetime import datetime, timezone

import requests

CACHE_PATH = '.chesscom_cache.json'
CACHE_TTL = 3600  # 1 hour

HEADERS = {'User-Agent': 'chess-analyzer/1.0 (personal project)'}


def fetch_games(username, months=3):
    """Fetch recent games from chess.com for `username`, covering the last `months` months.
    Returns a list of parsed game dicts. Results are cached for 1 hour."""
    cache = _load_cache()
    cache_key = f"{username}_{months}"
    entry = cache.get(cache_key)
    if entry and time.time() - entry['ts'] < CACHE_TTL:
        return entry['games']

    all_games = []
    now = datetime.now(timezone.utc)

    for offset in range(months):
        year, month = _month_offset(now.year, now.month, offset)
        games = _fetch_month(username, year, month)
        all_games.extend(games)

    cache[cache_key] = {'ts': time.time(), 'games': all_games}
    _save_cache(cache)
    return all_games


def _fetch_month(username, year, month):
    url = f"https://api.chess.com/pub/player/{username}/games/{year}/{month:02d}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        raw_games = r.json().get('games', [])
    except Exception as e:
        print(f"chess.com fetch failed for {year}/{month:02d}: {e}")
        return []

    parsed = []
    for g in raw_games:
        result = _parse_game(g, username)
        if result:
            parsed.append(result)
    return parsed


def _pgn_header(pgn, key):
    """Extract a value from a PGN header tag, e.g. [ECO "B90"] → 'B90'."""
    m = re.search(rf'\[{key}\s+"([^"]+)"\]', pgn)
    return m.group(1) if m else None


def _parse_game(g, username):
    pgn = g.get('pgn', '').strip()
    if not pgn:
        return None

    white = g.get('white', {})
    black = g.get('black', {})
    white_user = white.get('username', '').lower()
    black_user = black.get('username', '').lower()
    uname = username.lower()

    if uname == white_user:
        played_as = 'white'
        opponent = black.get('username', 'Unknown')
        my_result = white.get('result', '')
    elif uname == black_user:
        played_as = 'black'
        opponent = white.get('username', 'Unknown')
        my_result = black.get('result', '')
    else:
        return None

    draw_results = {'agreed', 'stalemate', 'repetition', 'insufficient', '50move', 'timevsinsufficient'}
    if my_result == 'win':
        result = 'win'
    elif my_result in draw_results:
        result = 'draw'
    else:
        result = 'loss'

    url = g.get('url', '')
    game_id = url.split('/')[-1] if url else None
    if not game_id:
        return None

    # Parse opening info from PGN headers
    eco     = _pgn_header(pgn, 'ECO')
    opening = _pgn_header(pgn, 'Opening')
    # Shorten verbose opening names (keep up to first colon only if short enough)
    if opening and len(opening) > 40:
        short = opening.split(':')[0].strip()
        opening = short if short else opening[:40]

    return {
        'id':         game_id,
        'url':        url,
        'pgn':        pgn,
        'played_as':  played_as,
        'opponent':   opponent,
        'result':     result,
        'time_class': g.get('time_class', 'unknown'),
        'end_time':   g.get('end_time', 0),
        'eco':        eco,
        'opening':    opening,
    }


def _month_offset(year, month, offset):
    month -= offset
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache):
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_PATH)
