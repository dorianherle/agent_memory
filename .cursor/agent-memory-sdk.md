# Agent Memory + Cursor SDK

`agent-memory cursor install` already wrote the project files this needs:

- `.cursor/mcp.json` — `agent-memory` MCP server
- `.cursor/hooks.json` — automatic saves (loaded from this project `cwd`)

## Local SDK (required opt-in for MCP)

Local Cursor SDK agents **do not** load `.cursor/mcp.json` by default.
Pass project settings so `memory_*` tools appear:

### Python

```python
from dotenv import load_dotenv
from cursor_sdk import Agent, LocalAgentOptions

load_dotenv()  # CURSOR_API_KEY

with Agent.create(
    model="composer-2.5",
    local=LocalAgentOptions(
        cwd=".",  # this project (has .cursor/mcp.json)
        setting_sources=["project"],  # ← loads agent-memory MCP
    ),
) as agent:
    result = agent.send("What do we already know?").wait()
    print(result.result)
```

### TypeScript

```ts
import { Agent } from "@cursor/sdk";

const agent = Agent.create({
  model: { id: "composer-2.5" },
  local: {
    cwd: process.cwd(),
    settingSources: ["project"], // ← loads agent-memory MCP
  },
});
```

## What you get

| Piece | How it activates |
|-------|------------------|
| MCP `memory_*` tools (including mid-turn `memory_save`) | `setting_sources=["project"]` / `settingSources: ["project"]` |
| Hooks auto-save (turn boundaries only) | automatic when `cwd` is this project (no settingSources needed) |

Inline `mcp_servers` / `mcpServers` also works if you prefer not to use ambient project settings.

Cloud SDK agents load project MCP/hooks from the repo differently — see Cursor SDK docs.
