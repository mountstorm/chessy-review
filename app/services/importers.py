"""Fetch recent games from Chess.com / Lichess public APIs as PGN text."""
from __future__ import annotations

import re
from typing import Optional

import httpx

UA = "chessy/0.2 (open-source chess review tool)"
_USER = re.compile(r"^[A-Za-z0-9_-]{2,40}$")


class ImportFailed(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _check_user(name: str) -> str:
    name = name.strip()
    if not _USER.match(name):
        raise ImportFailed("That doesn't look like a valid username.", 422)
    return name


def _upstream_error(site: str, e: httpx.HTTPError) -> ImportFailed:
    """Turn a failed call to Chess.com/Lichess into a message a player can act on (never raw httpx text)."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 429:
            return ImportFailed(f"{site} is rate-limiting requests right now. Wait a minute and try again.", 429)
        if code >= 500:
            return ImportFailed(f"{site} is having trouble right now (error {code}). Try again in a moment.", 502)
        return ImportFailed(f"{site} refused the request (error {code}).", 502)
    if isinstance(e, httpx.TimeoutException):
        return ImportFailed(f"{site} took too long to answer. Try again in a moment.", 504)
    return ImportFailed(f"Couldn't reach {site}. Check your connection and try again.", 502)


async def fetch_chesscom(username: str, max_games: int = 5, client: Optional[httpx.AsyncClient] = None) -> str:
    user = _check_user(username)
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": UA})
    try:
        r = await client.get(f"https://api.chess.com/pub/player/{user.lower()}/games/archives", headers={"User-Agent": UA})
        if r.status_code == 404:
            raise ImportFailed(f"Chess.com user '{user}' not found.", 404)
        r.raise_for_status()
        archives = r.json().get("archives", [])
        games = []
        for url in reversed(archives):               # newest month first
            try:
                ar = await client.get(url, headers={"User-Agent": UA})
                ar.raise_for_status()
            except httpx.HTTPError:
                if games:            # an older month failing shouldn't discard games we already have
                    break
                raise
            games.extend(reversed(ar.json().get("games", [])))
            if len(games) >= max_games:
                break
        pgns = [g["pgn"] for g in games[:max_games] if g.get("pgn")]
        if not pgns:
            raise ImportFailed(f"No recent games found for '{user}' on Chess.com.", 404)
        return "\n\n".join(pgns)
    except httpx.HTTPError as e:
        raise _upstream_error("Chess.com", e) from e
    finally:
        if own:
            await client.aclose()


async def fetch_lichess(username: str, max_games: int = 5, client: Optional[httpx.AsyncClient] = None) -> str:
    user = _check_user(username)
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, headers={"User-Agent": UA})
    try:
        r = await client.get(
            f"https://lichess.org/api/games/user/{user}",
            params={"max": max_games, "clocks": "true", "opening": "true", "literate": "false"},
            headers={"Accept": "application/x-chess-pgn", "User-Agent": UA},
        )
        if r.status_code == 404:
            raise ImportFailed(f"Lichess user '{user}' not found.", 404)
        r.raise_for_status()
        if not r.text.strip():
            raise ImportFailed(f"No recent games found for '{user}' on Lichess.", 404)
        return r.text
    except httpx.HTTPError as e:
        raise _upstream_error("Lichess", e) from e
    finally:
        if own:
            await client.aclose()
