"""Provider adapters. The agent loop is provider-agnostic: an adapter turns one model step into a stream of
TextDelta events followed by a StepEnd carrying any tool calls."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Optional, Protocol, Tuple, Union

from ..config import Settings
from .tools import TOOL_SPECS


class LLMError(RuntimeError):
    """A provider failure, already phrased for the end user."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class TextDelta:
    text: str


@dataclass
class StepEnd:
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: Optional[str] = None
    refused: bool = False


Event = Union[TextDelta, StepEnd]
ToolResult = Tuple[ToolCall, dict, bool]


class LLMAdapter(Protocol):
    provider: str
    def new_state(self, system: str, messages: List[dict]) -> Any: ...
    def step(self, state: Any) -> AsyncIterator[Event]: ...
    def add_tool_results(self, state: Any, results: List[ToolResult]) -> None: ...


# --------------------------------------------------------------------------- Anthropic
class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, settings: Settings, client: Any = None):
        import anthropic
        self._anthropic = anthropic
        self.s = settings
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in TOOL_SPECS]

    def new_state(self, system, messages):
        return {"system": system, "messages": list(messages)}

    async def step(self, state) -> AsyncIterator[Event]:
        a = self._anthropic
        kwargs: dict = dict(
            model=self.s.anthropic_model, max_tokens=8000, system=state["system"],
            tools=self.tools, messages=state["messages"],
            output_config={"effort": self.s.anthropic_effort},
        )
        api = self.client.messages
        if self.s.anthropic_refusal_fallback:
            # server-side refusal fallback: a declined request is re-run on a fallback model inside the same call
            api = self.client.beta.messages
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            async with api.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                final = await stream.get_final_message()
        except a.AuthenticationError:
            raise LLMError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.")
        except a.PermissionDeniedError:
            raise LLMError("The Anthropic API key lacks permission for this model.")
        except a.NotFoundError:
            raise LLMError(f"Model '{self.s.anthropic_model}' was not found. Check ANTHROPIC_MODEL.")
        except a.RateLimitError:
            raise LLMError("Anthropic rate limit reached. Try again in a moment.")
        except a.APIConnectionError:
            raise LLMError("Couldn't reach the Anthropic API (network error).")
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}")
        # keep the assistant turn exactly as returned (thinking / tool_use blocks must round-trip)
        state["messages"].append({"role": "assistant", "content": final.content})
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in final.content if b.type == "tool_use"]
        yield StepEnd(calls, final.stop_reason, refused=final.stop_reason == "refusal")

    def add_tool_results(self, state, results):
        state["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(r), "is_error": err}
            for c, r, err in results]})


# --------------------------------------------------------------------------- OpenAI-compatible (OpenAI + local)
class OpenAIAdapter:
    provider = "openai"

    def __init__(self, settings: Settings, client: Any = None):
        import openai
        self._openai = openai
        self.s = settings
        self.model = settings.openai_model
        self.client = client or openai.AsyncOpenAI(api_key=settings.openai_api_key)
        self.tools: Optional[list] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                        "parameters": t["schema"]}} for t in TOOL_SPECS]

    def new_state(self, system, messages):
        return {"messages": [{"role": "system", "content": system}] + list(messages)}

    def _connection_error(self) -> LLMError:
        return LLMError("Couldn't reach the OpenAI API (network error).")

    async def _open_stream(self, state):
        o = self._openai
        for attempt in (1, 2):
            kwargs: dict = dict(model=self.model, messages=state["messages"], stream=True)
            if self.tools and not state.get("no_tools"):
                kwargs["tools"] = self.tools
            if state.get("temperature") is not None:
                kwargs["temperature"] = state["temperature"]
            try:
                return await self.client.chat.completions.create(**kwargs)
            except o.BadRequestError as e:
                # many small local models don't support tool calling; fall back to engine facts injected in the prompt
                if attempt == 1 and self.tools and not state.get("no_tools") and "tool" in str(e).lower():
                    self.tools = None
                    continue
                raise

    async def step(self, state) -> AsyncIterator[Event]:
        o = self._openai
        text_parts: List[str] = []
        acc: dict = {}
        finish = None
        try:
            stream = await self._open_stream(state)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                ch = chunk.choices[0]
                if ch.delta.content:
                    text_parts.append(ch.delta.content)
                    yield TextDelta(ch.delta.content)
                for tc in ch.delta.tool_calls or []:
                    slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] = tc.function.name or slot["name"]
                        slot["args"] += tc.function.arguments or ""
                finish = ch.finish_reason or finish
        except o.AuthenticationError:
            raise LLMError(f"The {self.provider} API key was rejected.")
        except o.RateLimitError:
            raise LLMError(f"{self.provider} rate limit reached. Try again in a moment.")
        except o.APIConnectionError:
            raise self._connection_error()
        except o.APIStatusError as e:
            raise LLMError(f"{self.provider} error ({e.status_code}): {str(e)[:160]}")
        calls = []
        for i in sorted(acc):
            slot = acc[i]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(slot["id"] or f"call_{i}", slot["name"], args))
        msg: dict = {"role": "assistant", "content": "".join(text_parts) or None}
        if calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": json.dumps(c.input)}} for c in calls]
        state["messages"].append(msg)
        yield StepEnd(calls, finish, refused=finish == "content_filter")

    def add_tool_results(self, state, results):
        for c, r, _err in results:
            state["messages"].append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(r)})


class LocalAdapter(OpenAIAdapter):
    """Runs on your own machine through any OpenAI-compatible server (Ollama, LM Studio, llama.cpp).
    No API key, no data leaves the computer."""
    provider = "local"

    def __init__(self, settings: Settings, client: Any = None):
        import openai
        super().__init__(settings, client or openai.AsyncOpenAI(base_url=settings.local_llm_url, api_key="local", timeout=120))
        self.model = settings.local_llm_model

    def _connection_error(self) -> LLMError:
        return LLMError(f"Couldn't reach the local model at {self.s.local_llm_url}. Is Ollama running (`ollama serve`)?")


def make_adapter(settings: Settings) -> Optional[LLMAdapter]:
    """None means: no key configured, use the offline engine-grounded coach."""
    provider = settings.resolved_provider()
    if provider == "anthropic":
        return AnthropicAdapter(settings)
    if provider == "openai":
        return OpenAIAdapter(settings)
    if provider == "local":
        return LocalAdapter(settings)
    return None
