import json
from types import SimpleNamespace

from agent_memory import wrap
from agent_memory.tests.helpers import MemoryTestCase
from agent_memory.wrappers import MemoryEnhancedCursor


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


class FakeAgent:
    def __init__(self):
        self.sent = []
        self.entered = False
        self.exited = False

    def send(self, message, options=None, **kwargs):
        self.sent.append(message)
        return "run"

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args):
        self.exited = True
        return False


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
        client = wrap(fake, self.memory)

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
        client = wrap(fake, self.memory)

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
        client = wrap(fake, self.memory)

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
        client = wrap(fake, self.memory)

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

    def test_cursor_send_prepends_memory(self):
        fake = FakeAgent()
        agent = wrap(fake, self.memory)
        self.memory.save("Already knew the city")

        result = agent.send("Why does checkout double-charge?")

        self.assertEqual(result, "run")
        self.assertEqual(len(fake.sent), 1)
        self.assertIn("You work without chat history", fake.sent[0])
        self.assertIn("MEMORY STATE", fake.sent[0])
        self.assertIn("Already knew the city", fake.sent[0])
        self.assertIn("Why does checkout double-charge?", fake.sent[0])
        self.assertIsInstance(agent, MemoryEnhancedCursor)

    def test_cursor_context_manager_and_passthrough(self):
        fake = FakeAgent()
        fake.agent_id = "agent-123"

        with wrap(fake, self.memory) as agent:
            self.assertIsInstance(agent, MemoryEnhancedCursor)
            self.assertEqual(agent.agent_id, "agent-123")
        self.assertTrue(fake.entered)
        self.assertTrue(fake.exited)
