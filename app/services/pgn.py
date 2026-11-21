"""PGN -> structured game with a full variation tree (not just a linear move list)."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import chess
import chess.pgn

CLK_RE = re.compile(r"\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]")


class PGNError(ValueError):
    pass


@dataclass
class ParsedGame:
    headers: Dict[str, str]
    tree: Dict[str, Any]              # root node; children[0] is the mainline continuation
    pgn: str
    source_id: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def mainline(self) -> List[Dict[str, Any]]:
        return mainline_nodes(self.tree)


def parse_clock(comment: str) -> Optional[float]:
    m = CLK_RE.search(comment or "")
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _clean_comment(c: str) -> str:
    return re.sub(r"\[%[a-z]+\s+[^\]]*\]", "", c or "").strip()


def build_tree(game: chess.pgn.Game) -> Dict[str, Any]:
    """Iterative walk so a 400-ply game or deep variation never hits the recursion limit."""
    board = game.board()
    root = {"id": "root", "ply": 0, "san": None, "uci": None, "fen": board.fen(), "comment": _clean_comment(game.comment),
            "clock": None, "nags": [], "children": []}
    stack = [(game, root, board)]
    while stack:
        pnode, jnode, pboard = stack.pop()
        for idx, var in enumerate(pnode.variations):
            b = pboard.copy(stack=False)
            san = b.san(var.move)
            b.push(var.move)
            child = {
                "id": f"{jnode['id']}.{idx}" if jnode["id"] != "root" else str(idx),
                "ply": jnode["ply"] + 1, "san": san, "uci": var.move.uci(), "fen": b.fen(),
                "comment": _clean_comment(var.comment), "clock": parse_clock(var.comment),
                "nags": sorted(var.nags), "children": [],
            }
            jnode["children"].append(child)
            stack.append((var, child, b))
    return root


def mainline_nodes(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    out, node = [], tree
    while node["children"]:
        node = node["children"][0]
        out.append(node)
    return out


def count_variations(tree: Dict[str, Any]) -> int:
    n, stack = 0, [tree]
    while stack:
        t = stack.pop()
        n += max(0, len(t["children"]) - 1)
        stack.extend(t["children"])
    return n


def node_at(tree: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if node_id == "root":
        return tree
    node = tree
    for part in node_id.split("."):
        try:
            node = node["children"][int(part)]
        except (ValueError, IndexError):
            return None
    return node


def _opening_from_headers(h: Dict[str, str]) -> Optional[str]:
    if h.get("Opening") and h["Opening"] != "?":
        return h["Opening"]
    url = h.get("ECOUrl")
    if url:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        slug = re.split(r"-\d+\.", slug)[0]          # chess.com appends the move sequence
        return slug.replace("-", " ")
    return None


def _time_control(h: Dict[str, str]) -> Dict[str, Any]:
    tc = h.get("TimeControl", "")
    base = inc = None
    m = re.match(r"^(\d+)(?:\+(\d+))?$", tc)
    if m:
        base, inc = int(m.group(1)), int(m.group(2) or 0)
    return {"raw": tc or None, "base": base, "increment": inc}


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def extract_meta(h: Dict[str, str]) -> Dict[str, Any]:
    return {
        "white": h.get("White", "?"), "black": h.get("Black", "?"),
        "white_elo": _int(h.get("WhiteElo")), "black_elo": _int(h.get("BlackElo")),
        "result": h.get("Result", "*"), "eco": h.get("ECO"), "opening": _opening_from_headers(h),
        "time_control": _time_control(h), "event": h.get("Event"), "site": h.get("Site"),
        "date": h.get("UTCDate") or h.get("Date"), "termination": h.get("Termination"),
    }


def _source_id(h: Dict[str, str], pgn: str) -> str:
    site = h.get("Site", "")
    if "lichess.org/" in site:
        return site.rstrip("/").rsplit("/", 1)[-1][:8]
    link = h.get("Link")
    if link:
        return link
    return hashlib.sha1(pgn.encode()).hexdigest()[:16]


def iter_games(text: str) -> Iterator[ParsedGame]:
    stream = io.StringIO(text)
    while True:
        try:
            g = chess.pgn.read_game(stream)
        except Exception as e:  # malformed game: stop with a clear error
            raise PGNError(f"Could not parse PGN: {e}") from e
        if g is None:
            return
        if g.errors:
            first = g.errors[0]
            raise PGNError(f"PGN contains an illegal or unreadable move: {first}")
        if not g.variations:
            continue
        headers = dict(g.headers)
        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        pgn = g.accept(exporter)
        yield ParsedGame(headers, build_tree(g), pgn, _source_id(headers, pgn), extract_meta(headers))


def parse_pgn(text: str) -> List[ParsedGame]:
    games = list(iter_games(text))
    if not games:
        raise PGNError("No games with moves found in the PGN.")
    return games


def moves_to_pgn(moves: List[str]) -> str:
    """Accept loose input like 'e4 e5 Nf3' or '1. e4 e5 2. Nf3' and wrap as PGN."""
    return " ".join(moves)
