import json
from types import SimpleNamespace

from agent_memory.core import Memory
from agent_memory.prompt import MEMORY_INSTRUCTIONS
from agent_memory.tools import MEMORY_TOOL, MEMORY_TOOL_NAME, MEMORY_TOOLS, call_tool


def wrap(target, memory: Memory, **options):
    """Give a supported SDK long-term memory with one call.

    OpenAI (and compatible) clients::

        client = wrap(OpenAI(), memory)
        client.chat.completions.create(...)

    Cursor SDK, from the Agent class — registers the memory tool and
    prepends MEMORY STATE on every send::

        with wrap(Agent, memory, model="composer-2.5", cwd=".") as agent:
            agent.send("...").wait()

    Cursor SDK, from an agent that already exists — send injection only::

        agent = wrap(existing_agent, memory)
    """
    if isinstance(target, type) and callable(getattr(target, "create", None)):
        return wrap_agent(target, memory, **options)
    if hasattr(target, "chat"):
        return MemoryEnhancedOpenAI(target, memory)
    if callable(getattr(target, "send", None)):
        return MemoryEnhancedCursor(target, memory)
    raise ValueError(
        "unsupported target: pass an OpenAI-style client, the cursor_sdk "
        "Agent class, or a created agent"
    )


def memory_custom_tools(memory: Memory) -> dict:
    """The memory tool as a Cursor CustomTool."""
    from cursor_sdk import CustomTool

    def execute(args, context=None):
        return call_tool(memory, MEMORY_TOOL_NAME, dict(args or {}))

    spec = MEMORY_TOOL["function"]
    return {
        MEMORY_TOOL_NAME: CustomTool(
            description=spec["description"],
            input_schema=spec["parameters"],
            execute=execute,
        )
    }


def wrap_agent(agent_cls, memory: Memory, *, cwd=".", custom_tools=None, **options):
    """Create a Cursor agent with memory.

    The memory tool is registered as a custom tool at creation, and every
    send() prepends the memory instructions and the current MEMORY STATE.
    All other options go through to Agent.create unchanged.
    """
    from cursor_sdk import LocalAgentOptions

    tools = {**memory_custom_tools(memory), **dict(custom_tools or {})}
    local = options.pop("local", None)
    if local is None:
        local = LocalAgentOptions(cwd=cwd, custom_tools=tools)
    else:
        local.custom_tools = {**tools, **dict(local.custom_tools or {})}
    return MemoryEnhancedCursor(agent_cls.create(local=local, **options), memory)


class MemoryEnhancedCursor:
    """Wraps a Cursor agent so every send() carries the memory
    instructions and the current MEMORY STATE. Everything else passes
    through to the real agent."""

    def __init__(self, agent, memory: Memory) -> None:
        self.agent = agent
        self.memory = memory

    def send(self, message, *args, **kwargs):
        system, user = self.memory.messages(str(message))
        prompt = f"{system['content']}\n\n{user['content']}"
        return self.agent.send(prompt, *args, **kwargs)

    def __enter__(self):
        self.agent.__enter__()
        return self

    def __exit__(self, *args):
        return self.agent.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.agent, name)


class MemoryEnhancedOpenAI:
    """Drop-in wrapper around an OpenAI-compatible client.

    It only acts at the two boundaries of a request. Before the request
    goes out it injects the memory instructions, MEMORY STATE and the
    memory tool. When the model answers with a memory call, the wrapper
    runs it against the store and fires the next request. Text and your
    own tool calls are returned unchanged.
    """

    def __init__(self, client, memory: Memory) -> None:
        self.client = client
        self.memory = memory
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, messages, tools=None, **kwargs):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                if isinstance(message.get("content"), str):
                    self.memory.save_user(message["content"])
                break
        state_text = self.memory.render_state()
        messages = [
            {"role": "system", "content": MEMORY_INSTRUCTIONS},
            {
                "role": "system",
                "content": f"MEMORY STATE\n{state_text}",
            },
            *messages,
        ]
        tools = MEMORY_TOOLS + list(tools or [])
        if kwargs.get("stream"):
            return self._stream(messages, tools, kwargs)

        while True:
            self._refresh_state(messages)
            response = self.client.chat.completions.create(
                messages=messages,
                tools=tools,
                **kwargs,
            )
            message = response.choices[0].message
            calls = message.tool_calls or []
            if not calls or any(
                call.function.name != MEMORY_TOOL_NAME for call in calls
            ):
                return response
            messages.append(message)
            for call in calls:
                result = call_tool(
                    self.memory,
                    call.function.name,
                    json.loads(call.function.arguments),
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

    def _refresh_state(self, messages):
        # Own slot, replaced before every request; never accumulate packets.
        messages[1] = {"role": "system", "content": f"MEMORY STATE\n{self.memory.render_state()}"}

    def _stream(self, messages, tools, kwargs):
        while True:
            self._refresh_state(messages)
            stream = self.client.chat.completions.create(
                messages=messages,
                tools=tools,
                **kwargs,
            )
            calls = []
            for chunk in stream:
                yield chunk
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is not None and delta.tool_calls:
                    for piece in delta.tool_calls:
                        while len(calls) <= piece.index:
                            calls.append({"id": "", "name": "", "arguments": ""})
                        call = calls[piece.index]
                        if piece.id:
                            call["id"] = piece.id
                        if piece.function.name:
                            call["name"] = piece.function.name
                        if piece.function.arguments:
                            call["arguments"] += piece.function.arguments

            # After state: the stream is done. Only a pure memory call
            # keeps the loop going; anything else was the final answer.
            if not calls or any(
                call["name"] != MEMORY_TOOL_NAME for call in calls
            ):
                return
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                result = call_tool(
                    self.memory,
                    call["name"],
                    json.loads(call["arguments"]),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )
