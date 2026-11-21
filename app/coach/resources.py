"""Study resources, keyed by the kind of mistake the engine found in YOUR game.
All links are free. Lichess training themes: https://lichess.org/training/themes"""

LI = "https://lichess.org"
YT = "https://www.youtube.com/results?search_query="

THEMES = {
    "hanging": {
        "title": "Leaving pieces undefended (hanging pieces)",
        "habit": "Before every move, ask: after my move, what can the opponent capture for free? "
                 "Look at the square you move TO and at the piece you move FROM (it may have been guarding something).",
        "links": [
            ("Lichess puzzles: hanging piece", f"{LI}/training/hangingPiece"),
            ("Lichess puzzles: defensive move", f"{LI}/training/defensiveMove"),
            ("Lichess Learn: capture & protect", f"{LI}/learn"),
            ("Video: 'how to stop blundering pieces' ", YT + "how+to+stop+blundering+pieces+chess+beginner"),
        ],
    },
    "allowed_mate": {
        "title": "Walking into checkmate / king safety",
        "habit": "Before each move, list every CHECK the opponent could give afterwards. "
                 "If your king has few flight squares, assume the worst.",
        "links": [
            ("Lichess puzzles: mate in 1", f"{LI}/training/mateIn1"),
            ("Lichess puzzles: mate in 2", f"{LI}/training/mateIn2"),
            ("Lichess practice: checkmate patterns", f"{LI}/practice"),
            ("Video: 'back rank mate' patterns", YT + "back+rank+mate+patterns+beginner+chess"),
        ],
    },
    "mate_missed": {
        "title": "Missing forced checkmates",
        "habit": "When the enemy king looks exposed, scan EVERY check first, then every capture.",
        "links": [
            ("Lichess puzzles: mate in 2", f"{LI}/training/mateIn2"),
            ("Lichess practice: checkmates I & II", f"{LI}/practice"),
        ],
    },
    "missed_tactic": {
        "title": "Missing tactics (forks, pins, skewers, free captures)",
        "habit": "Use CCT every move: Checks, Captures, Threats, for BOTH sides. Slow down when the position is sharp.",
        "links": [
            ("Lichess puzzles: fork", f"{LI}/training/fork"),
            ("Lichess puzzles: pin", f"{LI}/training/pin"),
            ("Lichess puzzles: skewer", f"{LI}/training/skewer"),
            ("Lichess puzzles: discovered attack", f"{LI}/training/discoveredAttack"),
            ("Book: 'Winning Chess Tactics' (Seirawan), free to borrow", "https://archive.org/search?query=winning+chess+tactics+seirawan"),
        ],
    },
    "blown_advantage": {
        "title": "Not converting winning positions",
        "habit": "When ahead: trade pieces (not pawns), keep your king safe, and ask 'what is my opponent's best "
                 "counter-chance?' before going for the kill.",
        "links": [
            ("Lichess puzzles: advantage", f"{LI}/training/advantage"),
            ("Lichess puzzles: crushing", f"{LI}/training/crushing"),
            ("Video: 'how to convert a winning position'", YT + "how+to+convert+a+winning+position+chess+beginner"),
        ],
    },
    "opening": {
        "title": "Opening play",
        "habit": "Develop knights and bishops, castle by move ~8, fight for the center, and don't move the same piece twice without reason. "
                 "Pick ONE opening as White and one reply each to 1.e4 and 1.d4 and stick with them.",
        "links": [
            ("Lichess Opening Explorer (masters)", f"{LI}/analysis#explorer"),
            ("Lichess puzzles: opening", f"{LI}/training/opening"),
            ("Lichess Studies (community opening courses)", f"{LI}/study"),
            ("Book: 'Chess Fundamentals' (Capablanca), public domain", "https://www.gutenberg.org/ebooks/search/?query=chess+fundamentals"),
        ],
    },
    "endgame": {
        "title": "Endgames",
        "habit": "Learn king + pawn endgames and the Lucena/Philidor rook positions first. Activate your king in the endgame.",
        "links": [
            ("Lichess practice: pawn endgames, rook endgames", f"{LI}/practice"),
            ("Lichess puzzles: endgame", f"{LI}/training/endgame"),
            ("Book: 'Silman's Complete Endgame Course'", "https://archive.org/search?query=silman+complete+endgame+course"),
        ],
    },
    "positional": {
        "title": "Quiet / positional mistakes",
        "habit": "Improve your worst-placed piece, and avoid creating pawn weaknesses without a purpose.",
        "links": [
            ("Book: 'Logical Chess: Move by Move' (Chernev), every move explained", "https://archive.org/search?query=logical+chess+move+by+move"),
            ("Lichess puzzles: middlegame", f"{LI}/training/middlegame"),
            ("Video: Naroditsky 'Speedrun' series (explains ideas aloud)", YT + "naroditsky+speedrun+chess"),
        ],
    },
}

WEEKLY = [
    "Analyze every loss with this tool, and re-play the first mistake from the move before it.",
    "Puzzles, 15 to 20 a day, only on your weakest themes above. Accuracy beats speed.",
    "Play slower games (15+10 or 30+0) on Lichess. Rapid wins more rating at your level than blitz.",
    "Study one master game per week from the list in your report: play through it and predict each move.",
]
