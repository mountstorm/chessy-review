"""Where does a game leave master theory, and which master games look most like it?
Uses Lichess's free masters opening explorer (no token needed)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import chess
import httpx

API = "https://explorer.lichess.ovh/masters"
UA = "chessy/0.2 (open-source chess review tool)"
MAX_PLIES = 30


async def _fetch(client: httpx.AsyncClient, fen: str) -> Dict[str, Any]:
    r = await client.get(API, params={"fen": fen, "topGames": 5, "recentGames": 0, "moves": 8})
    if r.status_code == 429:
        await asyncio.sleep(2)
        r = await client.get(API, params={"fen": fen, "topGames": 5, "recentGames": 0, "moves": 8})
    r.raise_for_status()
    return r.json()


def _games(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for g in data.get("topGames", []):
        w, b = g.get("white", {}), g.get("black", {})
        out.append({"id": g["id"], "url": f"https://lichess.org/{g['id']}", "white": w.get("name", "?").replace("..", "."),
                    "white_elo": w.get("rating"), "black": b.get("name", "?").replace("..", "."),
                    "black_elo": b.get("rating"), "year": g.get("year"),
                    "result": {"white": "1-0", "black": "0-1"}.get(g.get("winner"), "1/2-1/2")})
    return out


async def walk_book(sans: List[str], client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": UA})
    board = chess.Board()
    res: Dict[str, Any] = {"opening": None, "eco": None, "plies_in_book": 0, "left_book_ply": None,
                           "played": None, "book_fen": None, "master_moves": [], "games": [], "error": None}
    last = None
    try:
        for i, san in enumerate(sans[:MAX_PLIES]):
            data = await _fetch(client, board.fen())
            total = data["white"] + data["draws"] + data["black"]
            if data.get("opening"):
                res["opening"], res["eco"] = data["opening"]["name"], data["opening"]["eco"]
            if total == 0:
                break
            last = (board.fen(), data)
            if not any(m["san"] == san for m in data["moves"]):
                res["left_book_ply"], res["played"] = i + 1, san
                break
            res["plies_in_book"] = i + 1
            board.push_san(san)
    except (httpx.HTTPError, ValueError) as e:
        res["error"] = f"Lichess masters lookup failed: {e}"
    finally:
        if own:
            await client.aclose()
    if last:
        fen, data = last
        res["book_fen"] = fen
        tot = max(1, data["white"] + data["draws"] + data["black"])
        res["master_moves"] = [{"san": m["san"], "games": m["white"] + m["draws"] + m["black"],
                                "share": round((m["white"] + m["draws"] + m["black"]) / tot, 3),
                                "avg_rating": m.get("averageRating"), "white": m["white"], "draws": m["draws"],
                                "black": m["black"]} for m in data["moves"][:6]]
        res["games"] = _games(data)
    return res
