import asyncio

import chess
import pytest

from app.engine.review import review_game, analyse_ply
from app.services.pgn import parse_pgn, mainline_nodes
from conftest import needs_engine

pytestmark = needs_engine


async def test_mate_in_one_and_cache(pool):
    b = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    ev = await pool.analyse(b, 10, 2)
    assert ev.best.move_uci == "a1a8" and ev.best.mate == 1 and ev.cp_white >= 9000
    misses = pool.cache.misses
    ev2 = await pool.analyse(b, 10, 2)
    assert ev2.best.move_uci == "a1a8" and pool.cache.misses == misses and pool.cache.hits >= 1
    assert (await pool.analyse(b, 10, 1)).multipv == 1          # different key, separate entry


async def test_terminal_positions(pool):
    mate = chess.Board("R5k1/5ppp/8/8/8/8/8/4K3 b - - 0 1")
    ev = await pool.analyse(mate, 8, 1)
    assert ev.terminal == "checkmate" and ev.cp_white > 9000 and ev.lines == []
    stale = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert (await pool.analyse(stale, 8, 1)).terminal == "stalemate"


async def test_concurrent_requests_do_not_block(pool):
    boards = [chess.Board()]
    for u in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"):
        b = boards[-1].copy()
        b.push_uci(u)
        boards.append(b)
    results = await asyncio.wait_for(asyncio.gather(*(pool.analyse(b, 9, 2) for b in boards * 2)), 60)
    assert len(results) == 14 and all(r.lines for r in results)


async def test_same_request_coalesced(pool):
    b = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    a, c = await asyncio.gather(pool.analyse(b, 11, 3), pool.analyse(b, 11, 3))
    assert a.lines[0].move_uci == c.lines[0].move_uci
    assert pool.searches == 1                      # two identical concurrent requests, one engine search


async def test_review_flags_blunder_and_streams_in_order(pool):
    # Scholar's-mate trap: 3...Nf6?? allows 4.Qxf7#
    g = parse_pgn("1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0")[0]
    events = [e async for e in review_game(pool, g.tree["fen"], mainline_nodes(g.tree), 12, 3)]
    assert [e["type"] for e in events] == ["start"] + ["move"] * 7 + ["summary"]
    moves = [e["move"] for e in events if e["type"] == "move"]
    assert [m["ply"] for m in moves] == list(range(1, 8))
    blunder = moves[5]
    assert blunder["san"] == "Nf6" and blunder["label"] == "blunder" and blunder["best"]["san"] == "Qxf6" or blunder["best"]["san"] == "g6"
    assert blunder["mover"] == "black" and blunder["loss"] > 0.5
    final = moves[6]
    assert final["san"] == "Qxf7#" and final["label"] in ("best", "great")
    assert final["eval_after"]["terminal"] == "checkmate" and final["eval_after"]["win"] > 0.99
    summary = events[-1]["summary"]
    assert summary["sides"]["black"]["counts"].get("blunder", 0) >= 1
    assert summary["sides"]["white"]["accuracy"] > summary["sides"]["black"]["accuracy"]
    assert len(summary["eval"]) == 8 and 6 in summary["key_moments"]


async def test_move_payload_has_ui_metadata(pool):
    g = parse_pgn('[TimeControl "300"]\n\n1. e4 {[%clk 0:04:58]} e5 2. Nf3 Nc6 3. Bb5 *')[0]
    events = [e async for e in review_game(pool, g.tree["fen"], mainline_nodes(g.tree), 10, 3)]
    m = [e["move"] for e in events if e["type"] == "move"][2]
    for key in ("fen_before", "fen_after", "arrows", "visuals", "motifs", "lines", "critical", "phase", "accuracy", "comment"):
        assert key in m
    assert set(m["visuals"]) == {"hanging", "king_safety", "mobility", "heatmap"} and len(m["visuals"]["heatmap"]) == 64
    assert len(m["lines"]) == 3 and m["arrows"]["played"]["from"] == "g1"
    assert m["arrows"]["threat"] is not None                       # opponent's threat if white passes
    first = [e["move"] for e in events if e["type"] == "move"][0]
    assert first["clock_left"] == 298.0


async def test_missed_mate_is_missed_win_and_records_motif(pool):
    # White has Ra8#; plays Kd2 instead.
    b = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    mv = chess.Move.from_uci("e1d2")
    before = await pool.analyse(b, 10, 3)
    after_b = b.copy(); after_b.push(mv)
    after = await pool.analyse(after_b, 10, 3)
    d = analyse_ply(1, b, mv, before, after, None, None)
    assert d["label"] in ("missed_win", "blunder", "mistake")
    assert "mate" in d["missed_motifs"] and d["best"]["san"] == "Ra8#"
