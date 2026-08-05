import json
from types import SimpleNamespace

from agent_memory import wrap_client
from agent_memory.tests.helpers import MemoryTestCase


def tool_call(name, arguments):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def reply(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def call_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)


class TestWrapper(MemoryTestCase):
    def test_memory_tool_loop(self):
        fake = FakeClient([
            reply(tool_calls=[
                tool_call(
                    "save",
                    {"text": "Read the file", "payload": None, "summary": False},
                )
            ]),
            reply(content="Done."),
        ])
        client = wrap_client(fake, self.memory)

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": "Investigate."}],
        )

        self.assertEqual(response.choices[0].message.content, "Done.")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(self.memory.state()[-1].message, "Read the file")

        first = fake.requests[0]
        self.assertIn("MEMORY STATE", first["messages"][1]["content"])
        names = [tool["function"]["name"] for tool in first["tools"]]
        self.assertEqual(names, ["save", "find", "branch"])

        second = fake.requests[1]
        self.assertEqual(second["messages"][-1]["role"], "tool")
        self.assertEqual(second["messages"][-1]["content"], "Saved.")

    def test_user_tools_pass_through(self):
        weather = {
            "type": "function",
            "function": {"name": "weather", "parameters": {}},
        }
        fake = FakeClient([
            reply(tool_calls=[tool_call("weather", {"city": "Paris"})]),
        ])
        client = wrap_client(fake, self.memory)

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": "Weather?"}],
            tools=[weather],
        )

        call = response.choices[0].message.tool_calls[0]
        self.assertEqual(call.function.name, "weather")
        self.assertEqual(len(fake.requests), 1)
        names = [tool["function"]["name"] for tool in fake.requests[0]["tools"]]
        self.assertEqual(names, ["save", "find", "branch", "weather"])

    def test_streaming_memory_tool_loop(self):
        fake = FakeClient([
            [
                chunk(tool_calls=[
                    call_delta(0, id="call_1", name="save",
                               arguments='{"text": "Moved to Neuch'),
                ]),
                chunk(tool_calls=[
                    call_delta(0, arguments='atel", "payload": null, "summary": false}'),
                ]),
            ],
            [chunk(content="Got"), chunk(content=" it.")],
        ])
        client = wrap_client(fake, self.memory)

        stream = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": "I moved. Remember this."}],
            stream=True,
        )
        chunks = list(stream)
        text = "".join(c.choices[0].delta.content or "" for c in chunks)

        self.assertEqual(text, "Got it.")
        # Everything passes through: 2 tool call chunks + 2 text chunks.
        self.assertEqual(len(chunks), 4)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(self.memory.state()[-1].message, "Moved to Neuchatel")
        self.assertTrue(fake.requests[1]["stream"])
        self.assertEqual(fake.requests[1]["messages"][-1]["content"], "Saved.")

    def test_streaming_user_tools_pass_through(self):
        fake = FakeClient([
            [
                chunk(tool_calls=[
                    call_delta(0, id="call_9", name="weather", arguments="{}"),
                ]),
            ],
        ])
        client = wrap_client(fake, self.memory)

        stream = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": "Weather?"}],
            stream=True,
        )
        chunks = list(stream)

        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(len(chunks), 1)
        piece = chunks[0].choices[0].delta.tool_calls[0]
        self.assertEqual(piece.function.name, "weather")
