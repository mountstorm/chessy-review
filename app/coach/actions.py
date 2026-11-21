"""UI action commands. The LLM (or offline coach) points at the board with inline tags:

    [[arrow:e2e4:green]]   [[hl:f7,g7]]   [[clear]]

`TagStream` strips the tags out of the streamed prose (even when a tag is split across chunks),
validates them and turns them into {"draw_arrows": [...], "highlight_squares": [...]} for the frontend.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import chess

COLORS = {"green", "red", "blue", "yellow", "orange"}
_TAG = re.compile(r"\[\[(arrow|hl|clear)(?::([^\]]*))?\]\]")


@dataclass
class UiActions:
    arrows: List[Dict[str, str]] = field(default_factory=list)
    highlights: List[str] = field(default_factory=list)
    clear: bool = False

    def is_empty(self) -> bool:
        return not (self.arrows or self.highlights or self.clear)

    def merge(self, other: "UiActions") -> None:
        self.arrows += other.arrows
        for h in other.highlights:
            if h not in self.highlights:
                self.highlights.append(h)
        self.clear = self.clear or other.clear

    def to_dict(self) -> Dict:
        d: Dict = {}
        if self.arrows:
            d["draw_arrows"] = self.arrows
        if self.highlights:
            d["highlight_squares"] = self.highlights
        if self.clear:
            d["clear"] = True
        return d


def _sq(s: str) -> Optional[str]:
    s = s.strip().lower()
    try:
        chess.parse_square(s)
        return s
    except ValueError:
        return None


def parse_tag(kind: str, arg: Optional[str]) -> Optional[UiActions]:
    """Return validated actions, or None if the tag is malformed (so it is dropped, never trusted)."""
    acts = UiActions()
    if kind == "clear":
        acts.clear = True
        return acts
    parts = (arg or "").split(":")
    if kind == "arrow":
        move = parts[0].strip().lower().replace("-", "")
        color = parts[1].strip().lower() if len(parts) > 1 else "green"
        if len(move) != 4 or color not in COLORS:
            return None
        a, b = _sq(move[:2]), _sq(move[2:])
        if not a or not b or a == b:
            return None
        acts.arrows.append({"from": a, "to": b, "color": color})
        return acts
    if kind == "hl":
        squares = [_sq(x) for x in parts[0].split(",") if x.strip()]
        if not squares or any(s is None for s in squares):
            return None
        acts.highlights = list(dict.fromkeys(squares))
        return acts
    return None


def parse_all(text: str) -> Tuple[str, UiActions]:
    """Non-streaming convenience: strip every tag from `text`."""
    s = TagStream()
    out, acts = s.feed(text)
    tail, acts2 = s.flush()
    acts.merge(acts2)
    return out + tail, acts


class TagStream:
    MAX_TAG = 80

    def __init__(self):
        self._buf = ""

    def feed(self, chunk: str) -> Tuple[str, UiActions]:
        self._buf += chunk
        out: List[str] = []
        acts = UiActions()
        while True:
            i = self._buf.find("[[")
            if i < 0:
                if self._buf.endswith("["):
                    out.append(self._buf[:-1])
                    self._buf = "["
                else:
                    out.append(self._buf)
                    self._buf = ""
                break
            out.append(self._buf[:i])
            j = self._buf.find("]]", i)
            if j < 0:
                if len(self._buf) - i > self.MAX_TAG:      # never closes: it was just text
                    out.append("[[")
                    self._buf = self._buf[i + 2:]
                    continue
                self._buf = self._buf[i:]
                break
            raw = self._buf[i:j + 2]
            self._buf = self._buf[j + 2:]
            m = _TAG.fullmatch(raw)
            parsed = parse_tag(m.group(1), m.group(2)) if m else None
            if parsed:
                acts.merge(parsed)
            # malformed tags are dropped silently: the model never shows raw markup to the user
        return "".join(out), acts

    def flush(self) -> Tuple[str, UiActions]:
        rest, self._buf = self._buf, ""
        return ("" if rest.startswith("[[") else rest), UiActions()
