import json
from types import SimpleNamespace

from agent_memory.core import Memory
from agent_memory.prompt import MEMORY_INSTRUCTIONS, render
from agent_memory.tools import MEMORY_TOOLS, call_tool

MEMORY_TOOL_NAMES = {tool["function"]["name"] for tool in MEMORY_TOOLS}


def wrap_client(client, memory: Memory):
    """Wrap an SDK client so its requests get memory injected and handled."""
    if hasattr(client, "chat"):
        return MemoryEnhancedOpenAI(client, memory)
    raise ValueError(
        "unsupported client: expected something with chat.completions.create"
    )


class MemoryEnhancedOpenAI:
    """Drop-in wrapper around an OpenAI-compatible client.

    It only acts at the two boundaries of a request. Before the request
    goes out it injects the memory instructions, MEMORY STATE and the
    memory tools. When the model answers with a save / find / branch
    call, the wrapper runs it against the store and fires the next
    request. Text and your own tool calls are returned unchanged.
    """

    def __init__(self, client, memory: Memory) -> None:
        self.client = client
        self.memory = memory
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, messages, tools=None, **kwargs):
        messages = [
            {"role": "system", "content": MEMORY_INSTRUCTIONS},
            {
                "role": "system",
                "content": f"MEMORY STATE\n{render(self.memory.state())}",
            },
            *messages,
        ]
        tools = MEMORY_TOOLS + list(tools or [])
        if kwargs.get("stream"):
            return self._stream(messages, tools, kwargs)

        while True:
            response = self.client.chat.completions.create(
                messages=messages,
                tools=tools,
                **kwargs,
            )
            message = response.choices[0].message
            calls = message.tool_calls or []
            if not calls or any(
                call.function.name not in MEMORY_TOOL_NAMES for call in calls
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

    def _stream(self, messages, tools, kwargs):
        while True:
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
                call["name"] not in MEMORY_TOOL_NAMES for call in calls
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
