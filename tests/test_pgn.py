import pytest

from app.services import pgn as p

PGN = '''[Event "Test"]
[White "Alice"]
[Black "Bob"]
[WhiteElo "1500"]
[BlackElo "1480"]
[Result "1-0"]
[ECO "C50"]
[Opening "Italian Game"]
[TimeControl "180+2"]

1. e4 {[%clk 0:03:00]} e5 {[%clk 0:02:59.5]} 2. Nf3 (2. Bc4 Nf6 (2... Bc5 3. c3) 3. d3) 2... Nc6 {good} 3. Bb5 1-0
'''


def test_tree_has_nested_variations():
    g = p.parse_pgn(PGN)[0]
    main = g.mainline()
    assert [n["san"] for n in main] == ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    nf3 = main[2]
    parent = p.node_at(g.tree, main[1]["id"])
    assert [c["san"] for c in parent["children"]] == ["Nf3", "Bc4"]          # variation sits beside mainline
    bc4 = parent["children"][1]
    assert [c["san"] for c in bc4["children"]] == ["Nf6", "Bc5"]             # nested variation inside variation
    assert bc4["children"][1]["children"][0]["san"] == "c3"
    assert p.count_variations(g.tree) == 2


def test_ids_resolve_and_fens_match_positions():
    g = p.parse_pgn(PGN)[0]
    for n in g.mainline():
        assert p.node_at(g.tree, n["id"]) is n
    assert g.mainline()[0]["fen"].startswith("rnbqkbnr/pppppppp/8/8/4P3")


def test_metadata_clock_and_time_control():
    g = p.parse_pgn(PGN)[0]
    m = g.meta
    assert (m["white"], m["black"], m["white_elo"], m["opening"], m["eco"]) == ("Alice", "Bob", 1500, "Italian Game", "C50")
    assert m["time_control"] == {"raw": "180+2", "base": 180, "increment": 2}
    assert g.mainline()[0]["clock"] == 180.0
    assert g.mainline()[1]["clock"] == pytest.approx(179.5)
    assert "[%clk" not in g.mainline()[0]["comment"]


def test_multiple_games_and_errors():
    two = PGN + "\n\n" + PGN.replace("Alice", "Carol")
    assert len(p.parse_pgn(two)) == 2
    with pytest.raises(p.PGNError):
        p.parse_pgn("1. e4 e5 2. Nf6 *")        # illegal move
    with pytest.raises(p.PGNError):
        p.parse_pgn("   ")


def test_deep_game_no_recursion_error():
    import chess
    b = chess.Board()
    moves = []
    for _ in range(400):                         # shuffle knights: 800 plies
        for u in ("g1f3", "g8f6", "f3g1", "f6g8"):
            moves.append(b.san(chess.Move.from_uci(u)))
            b.push_uci(u)
    text = " ".join(f"{i // 2 + 1}. {m}" if i % 2 == 0 else m for i, m in enumerate(moves[:1200]))
    g = p.parse_pgn(text)[0]
    assert len(g.mainline()) == 1200


def test_chesscom_opening_slug():
    pgn = '[White "a"]\n[Black "b"]\n[ECOUrl "https://www.chess.com/openings/Sicilian-Defense-Open-Najdorf-Variation-5.Nc3-a6"]\n\n1. e4 c5 *'
    assert p.parse_pgn(pgn)[0].meta["opening"].startswith("Sicilian Defense Open Najdorf Variation")
