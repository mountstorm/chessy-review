import chess
import pytest

from app.coach.actions import TagStream, parse_all, parse_tag
from app.coach.agent import run_coach
from app.coach.llm import StepEnd, TextDelta, ToolCall
from app.coach.tools import ToolExecutor, parse_move
from app.config import get_settings
from app.models.schemas import CoachRequest
from conftest import needs_engine


# ---------------------------------------------------------------- UI action parsing
def test_parse_valid_tags():
    text, acts = parse_all("Look at [[arrow:e2e4:green]] and [[hl:f7,g7]] here[[clear]].")
    assert text == "Look at  and  here."
    assert acts.arrows == [{"from": "e2", "to": "e4", "color": "green"}]
    assert acts.highlights == ["f7", "g7"] and acts.clear
    assert acts.to_dict() == {"draw_arrows": acts.arrows, "highlight_squares": ["f7", "g7"], "clear": True}


@pytest.mark.parametrize("kind,arg", [("arrow", "e9e4"), ("arrow", "e2e2"), ("arrow", "e2e4:purple"),
                                      ("arrow", "e2"), ("hl", "z9"), ("hl", ""), ("hl", "f7,zz")])
def test_invalid_tags_rejected(kind, arg):
    assert parse_tag(kind, arg) is None


def test_malformed_tags_are_stripped_not_shown():
    text, acts = parse_all("Try [[arrow:e9e4:green]] now [[bogus:x]].")
    assert "[[" not in text and acts.is_empty()


def test_tags_split_across_stream_chunks():
    s = TagStream()
    out, arrows = [], []
    for chunk in ["Play ", "[", "[arr", "ow:g1f3:gre", "en]] then [[", "hl:e5]] ok [x] done"]:
        t, a = s.feed(chunk)
        out.append(t)
        arrows += a.arrows + [{"hl": h} for h in a.highlights]
    t, _ = s.flush()
    out.append(t)
    assert "".join(out) == "Play  then  ok [x] done"
    assert arrows == [{"from": "g1", "to": "f3", "color": "green"}, {"hl": "e5"}]


def test_unclosed_tag_flushes_as_nothing_not_garbage():
    s = TagStream()
    t1, _ = s.feed("hello [[arrow:e2")
    t2, _ = s.flush()
    assert (t1 + t2) == "hello "


# ---------------------------------------------------------------- tools
def test_parse_move_san_and_uci():
    b = chess.Board()
    assert parse_move(b, "Nf3").uci() == "g1f3" and parse_move(b, "g1f3").uci() == "g1f3" and parse_move(b, "Ke5") is None


@needs_engine
async def test_tools_are_engine_grounded(pool):
    ex = ToolExecutor(pool)
    legal, err = await ex.run("get_legal_moves", {"fen": chess.STARTING_FEN})
    assert not err and legal["count"] == 20
    r, err = await ex.run("evaluate_fen", {"fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "depth": 10})
    assert not err and r["lines"][0]["move"] == "Ra8#" and r["lines"][0]["mate"] == 1
    t, err = await ex.run("test_candidate_move", {"fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "move": "Kd2", "depth": 10})
    assert not err and t["legal"] and not t["is_engine_best"] and t["best_move"] == "Ra8#" and t["win_prob_loss"] > 0.1
    ok, _ = await ex.run("test_candidate_move", {"fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "move": "Ra8#", "depth": 10})
    assert ok["is_engine_best"] and ok["classification"] in ("best", "great")


@needs_engine
async def test_tool_errors_are_data_not_crashes(pool):
    ex = ToolExecutor(pool)
    r, err = await ex.run("evaluate_fen", {"fen": "not a fen"})
    assert err and "invalid FEN" in r["error"]
    r, err = await ex.run("evaluate_fen", {"depth": 14})
    assert err and "bad arguments" in r["error"]
    r, err = await ex.run("nope", {})
    assert err
    r, err = await ex.run("test_candidate_move", {"fen": chess.STARTING_FEN, "move": "Ke5"})
    assert not err and r["legal"] is False and "Nf3" in r["legal_moves"]


# ---------------------------------------------------------------- agent: offline mode
async def collect(gen):
    return [e async for e in gen]


@needs_engine
async def test_offline_explain_is_grounded_and_draws_arrow(pool):
    req = CoachRequest(current_fen="6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", mode="explain", move_history=[])
    evs = await collect(run_coach(req, ToolExecutor(pool), None, get_settings()))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "grounding" and kinds[-1] == "done"
    assert evs[0]["best"] == "Ra8#" and evs[0]["provider"] == "offline"
    text = "".join(e["delta"] for e in evs if e["type"] == "text")
    assert "Ra8#" in text and "checkmate" in text.lower()
    ui = [e for e in evs if e["type"] == "ui"][0]
    assert ui["draw_arrows"][0] == {"from": "a1", "to": "a8", "color": "green"}


@needs_engine
async def test_offline_socratic_ladder_does_not_leak_answer_early(pool):
    fen = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"
    texts = []
    for n in range(4):
        hist = [{"role": "assistant", "content": "hint"} for _ in range(n)]
        req = CoachRequest(current_fen=fen, mode="socratic_hint", history=hist)
        evs = await collect(run_coach(req, ToolExecutor(pool), None, get_settings()))
        texts.append(("".join(e["delta"] for e in evs if e["type"] == "text"), [e for e in evs if e["type"] == "ui"]))
    assert all("Ra8" not in t for t, _ in texts[:3])
    assert "Ra8#" in texts[3][0] and texts[3][1][0]["draw_arrows"]


@needs_engine
async def test_offline_quiz_grades_user_move(pool):
    fen = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"
    good = await collect(run_coach(CoachRequest(current_fen=fen, mode="quiz_me", user_message="Ra8#"), ToolExecutor(pool), None, get_settings()))
    bad = await collect(run_coach(CoachRequest(current_fen=fen, mode="quiz_me", user_message="I'd play Kd2"), ToolExecutor(pool), None, get_settings()))
    ask = await collect(run_coach(CoachRequest(current_fen=fen, mode="quiz_me"), ToolExecutor(pool), None, get_settings()))
    t = lambda evs: "".join(e["delta"] for e in evs if e["type"] == "text")
    assert "Correct" in t(good) and "Not quite" in t(bad) and "Ra8#" in t(bad) and "Quiz time" in t(ask)
    assert "Ra8" not in t(ask)


# ---------------------------------------------------------------- agent: LLM loop with a scripted adapter
class ScriptedAdapter:
    """Pretends to be an LLM: round 1 calls two tools, round 2 answers with UI tags split across chunks."""
    provider = "scripted"

    def __init__(self):
        self.rounds = 0
        self.seen_results = []

    def new_state(self, system, messages):
        self.system, self.first_user = system, messages[-1]["content"]
        return {}

    async def step(self, state):
        self.rounds += 1
        if self.rounds == 1:
            yield TextDelta("Let me check. ")
            yield StepEnd([ToolCall("t1", "test_candidate_move", {"fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "move": "Kd2", "depth": 9}),
                           ToolCall("t2", "get_legal_moves", {"fen": "bad fen"})], "tool_use")
        else:
            for c in ["The engine says Ra8# wins. [[arr", "ow:a1a8:gr", "een]] See? [[hl:g8]]"]:
                yield TextDelta(c)
            yield StepEnd([], "end_turn")

    def add_tool_results(self, state, results):
        self.seen_results = results


@needs_engine
async def test_llm_loop_executes_tools_strips_tags_and_survives_tool_errors(pool):
    ad = ScriptedAdapter()
    req = CoachRequest(current_fen="6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", mode="explain", user_message="why not Kd2?")
    evs = await collect(run_coach(req, ToolExecutor(pool), ad, get_settings()))
    kinds = [e["type"] for e in evs]
    assert kinds.count("tool") == 2 and kinds.count("tool_result") == 2 and kinds[-1] == "done"
    results = {e["name"]: e for e in evs if e["type"] == "tool_result"}
    assert results["test_candidate_move"]["ok"] and not results["get_legal_moves"]["ok"]       # error returned as data
    assert ad.seen_results[1][2] is True                                                         # flagged is_error to the model
    text = "".join(e["delta"] for e in evs if e["type"] == "text")
    assert text == "Let me check. The engine says Ra8# wins.  See? " and "[[" not in text
    ui = [e for e in evs if e["type"] == "ui"]
    assert len(ui) == 1                       # both tags completed in the same chunk, so they arrive merged
    assert ui[0]["draw_arrows"] == [{"from": "a1", "to": "a8", "color": "green"}] and ui[0]["highlight_squares"] == ["g8"]
    # grounding was injected before the model ever spoke
    assert "ENGINE FACTS" in ad.first_user and "Ra8#" in ad.first_user and "USER: why not Kd2?" in ad.first_user
    assert "never state a tactic" in ad.system.lower() or "Never state a tactic" in ad.system


class FailingAdapter:
    provider = "broken"
    def new_state(self, s, m): return {}
    async def step(self, state):
        from app.coach.llm import LLMError
        raise LLMError("The Anthropic API key was rejected.")
        yield
    def add_tool_results(self, *a): pass


@needs_engine
async def test_llm_failure_falls_back_to_offline_coach(pool):
    req = CoachRequest(current_fen="6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", mode="explain")
    evs = await collect(run_coach(req, ToolExecutor(pool), FailingAdapter(), get_settings()))
    kinds = [e["type"] for e in evs]
    assert "notice" in kinds and "Ra8#" in "".join(e.get("delta", "") for e in evs) and kinds[-1] == "done"


# ---------------------------------------------------------------- local (OpenAI-compatible) adapter
from types import SimpleNamespace as NS


def _chunk(content=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=None), finish_reason=finish)])


class _FakeCompletions:
    """Mimics Ollama with a model that rejects tool definitions."""
    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        import httpx
        import openai
        self.calls.append(kw)
        if "tools" in kw:
            req = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
            raise openai.BadRequestError("registry.ollama.ai/library/tiny does not support tools",
                                         response=httpx.Response(400, request=req), body=None)

        async def gen():
            for c in ["The best move is ", "Ra8#", ". Tiny model says Nxe5 too."]:
                yield _chunk(c)
            yield _chunk(None, "stop")
        return gen()


async def test_local_adapter_falls_back_when_model_has_no_tool_support():
    from app.coach.llm import LocalAdapter
    fake = NS(chat=NS(completions=_FakeCompletions()))
    ad = LocalAdapter(get_settings(), client=fake)
    assert ad.provider == "local" and ad.model == get_settings().local_llm_model
    st = ad.new_state("sys", [{"role": "user", "content": "hi"}])
    evs = [e async for e in ad.step(st)]
    assert "".join(e.text for e in evs if isinstance(e, TextDelta)).startswith("The best move is Ra8#")
    assert isinstance(evs[-1], StepEnd) and evs[-1].tool_calls == []
    assert ad.tools is None and len(fake.chat.completions.calls) == 2      # retried once without tools, remembered


def test_unverified_moves_flags_invented_moves_only():
    from app.coach.agent import unverified_moves
    b = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    facts = {"lines": [{"pv": ["Ra8#"]}]}
    assert unverified_moves("Play Ra8# now, or Kd2 if you like.", b, facts, []) == []        # Ra8# in engine line, Kd2 legal
    assert unverified_moves("Then Nxe5 wins and Qh7 too.", b, facts, []) == ["Nxe5", "Qh7"]
    assert unverified_moves("the pawn on e5 and the h7 square", b, facts, []) == []            # plain squares are prose
    assert unverified_moves("e4 was played earlier.", b, facts, ["e4"]) == []


class _Say:
    """OpenAI-compatible fake that streams fixed text (no tool support, like many small models)."""
    def __init__(self, parts):
        self.parts, self.calls = parts, []

    async def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})      # snapshot: the adapter mutates the list later
        async def gen():
            for p in self.parts:
                yield _chunk(p)
            yield _chunk(None, "stop")
        return gen()


def _local(parts):
    from app.coach.llm import LocalAdapter
    fake = _Say(parts)
    return LocalAdapter(get_settings(), client=NS(chat=NS(completions=fake))), fake


MATE_FEN = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"


@needs_engine
async def test_local_rewrite_is_streamed_when_it_sticks_to_the_facts(pool):
    ad, fake = _local(["White can just play ", "Ra8#, ", "it is checkmate on the back rank."])
    req = CoachRequest(current_fen=MATE_FEN, mode="explain", user_message="what should I play?")
    evs = await collect(run_coach(req, ToolExecutor(pool), ad, get_settings()))
    kinds = [e["type"] for e in evs]
    assert "replace" not in kinds and "notice" not in kinds and kinds[-1] == "done"
    assert "".join(e["delta"] for e in evs if e["type"] == "text").startswith("White can just play Ra8#")
    sent = fake.calls[0]
    assert "tools" not in sent and sent["temperature"] == 0.3                     # no tool calling for small models
    assert "FACTS (from Stockfish)" in sent["messages"][-1]["content"] and "Ra8#" in sent["messages"][-1]["content"]
    assert any(e["type"] == "ui" and e["draw_arrows"] for e in evs)               # arrows come from the engine, not the model


@needs_engine
async def test_local_rewrite_with_invented_move_is_replaced_by_engine_text(pool):
    ad, _ = _local(["The knight takes ", "Nxe5 and wins."])
    req = CoachRequest(current_fen=MATE_FEN, mode="explain")
    evs = await collect(run_coach(req, ToolExecutor(pool), ad, get_settings()))
    rep = [e for e in evs if e["type"] == "replace"]
    assert rep and "Ra8#" in rep[0]["text"] and "Nxe5" not in rep[0]["text"]
    assert any(e["type"] == "notice" and "Nxe5" in e["message"] for e in evs)


@needs_engine
async def test_local_hint_and_quiz_modes_never_call_the_model(pool):
    ad, fake = _local(["should never be used"])
    for mode in ("socratic_hint", "quiz_me"):
        evs = await collect(run_coach(CoachRequest(current_fen=MATE_FEN, mode=mode), ToolExecutor(pool), ad, get_settings()))
        assert evs[-1]["type"] == "done" and "should never" not in "".join(e.get("delta", "") for e in evs)
    assert fake.calls == []


@needs_engine
async def test_local_server_down_falls_back_to_engine_text(pool):
    import httpx, openai
    class Down:
        async def create(self, **kw):
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434/v1"))
    from app.coach.llm import LocalAdapter
    ad = LocalAdapter(get_settings(), client=NS(chat=NS(completions=Down())))
    evs = await collect(run_coach(CoachRequest(current_fen=MATE_FEN, mode="explain"), ToolExecutor(pool), ad, get_settings()))
    assert any(e["type"] == "notice" and "ollama" in e["message"].lower() for e in evs)
    assert "Ra8#" in "".join(e.get("delta", "") for e in evs)


# ---------------------------------------------------------------- hallucination guard on the cloud (tool-calling) path
class _Cloud:
    """Stand-in for the Anthropic/OpenAI adapter: optional tool call in round 1, then a fixed answer."""
    provider = "anthropic"

    def __init__(self, answer, tool_call=None, refused=False):
        self.answer, self.tool_call, self.refused, self.rounds = answer, tool_call, refused, 0

    def new_state(self, system, messages):
        return {}

    async def step(self, state):
        self.rounds += 1
        if self.tool_call and self.rounds == 1:
            yield StepEnd([self.tool_call], "tool_use")
            return
        for c in self.answer:
            yield TextDelta(c)
        yield StepEnd([], "refusal" if self.refused else "end_turn", refused=self.refused)

    def add_tool_results(self, state, results):
        pass


async def _cloud(pool, ad, mode="explain"):
    req = CoachRequest(current_fen=MATE_FEN, mode=mode, user_message="what should I play?")
    return await collect(run_coach(req, ToolExecutor(pool), ad, get_settings()))


@needs_engine
async def test_cloud_answer_with_invented_move_is_replaced_by_engine_text(pool):
    evs = await _cloud(pool, _Cloud(["The knight takes ", "Nxe5 and wins."]))
    rep = [e for e in evs if e["type"] == "replace"]
    assert rep and "Ra8#" in rep[0]["text"] and "Nxe5" not in rep[0]["text"]
    assert any(e["type"] == "notice" and "Nxe5" in e["message"] for e in evs)
    assert evs[-1]["type"] == "done"


@needs_engine
async def test_cloud_replacement_clears_arrows_the_model_drew(pool):
    evs = await _cloud(pool, _Cloud(["[[arrow:e4e5:red]]Nxe5 wins."]))
    kinds = [e["type"] for e in evs]
    model_ui = next(i for i, e in enumerate(evs) if e["type"] == "ui")           # drawn while streaming
    clear = next(i for i, e in enumerate(evs) if e["type"] == "ui" and e.get("clear"))
    assert model_ui < clear < kinds.index("replace")
    assert any(e["type"] == "ui" and e.get("draw_arrows") for e in evs[clear:])   # the engine's own arrow follows


@needs_engine
async def test_cloud_may_repeat_moves_its_own_tool_calls_returned(pool):
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"           # Nf3 exists there, not in MATE_FEN
    call = ToolCall("t1", "test_candidate_move", {"fen": start, "move": "Nf3", "depth": 9})
    evs = await _cloud(pool, _Cloud(["On the starting position Nf3 is fine, but here Ra8# wins."], call))
    kinds = [e["type"] for e in evs]
    assert "replace" not in kinds and "notice" not in kinds and kinds[-1] == "done"


@needs_engine
async def test_cloud_answer_that_sticks_to_the_facts_is_untouched(pool):
    evs = await _cloud(pool, _Cloud(["Ra8# is checkmate on the back rank."]))
    kinds = [e["type"] for e in evs]
    assert "replace" not in kinds and "notice" not in kinds
    assert "".join(e["delta"] for e in evs if e["type"] == "text") == "Ra8# is checkmate on the back rank."


@needs_engine
async def test_cloud_guard_also_covers_hint_mode(pool):
    evs = await _cloud(pool, _Cloud(["Try Qh7, it wins."]), mode="socratic_hint")
    assert any(e["type"] == "replace" for e in evs) and any(e["type"] == "notice" and "Qh7" in e["message"] for e in evs)


@needs_engine
async def test_cloud_refusal_is_not_second_guessed(pool):
    evs = await _cloud(pool, _Cloud(["Nxe5"], refused=True))
    kinds = [e["type"] for e in evs]
    assert "error" in kinds and "replace" not in kinds


# ---------------------------------------------------------------- the grounding event reports how deep Stockfish looked
@needs_engine
async def test_grounding_event_carries_the_engine_depth_the_facts_came_from(pool):
    for adapter in (None, _Cloud(["Ra8# is mate."]), _local(["White can play ", "Ra8#."])[0]):
        evs = await collect(run_coach(CoachRequest(current_fen=MATE_FEN, mode="explain"), ToolExecutor(pool), adapter, get_settings()))
        g = evs[0]
        assert g["type"] == "grounding" and g["depth"] == 14                       # evaluate_fen is asked for depth 14
        assert g["provider"] == (adapter.provider if adapter else "offline")


@needs_engine
async def test_grounding_comes_before_any_text_so_the_tag_never_precedes_the_engine(pool):
    evs = await collect(run_coach(CoachRequest(current_fen=MATE_FEN, mode="socratic_hint"), ToolExecutor(pool), None, get_settings()))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "grounding" and kinds.index("grounding") < kinds.index("text")
