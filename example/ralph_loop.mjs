import { Codex } from "./node_modules/@openai/codex-sdk/dist/index.js";
import { execFileSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";

const WORKSPACE = "/Users/dorianherle/Documents/Personal/agent_memory/example";
const AGENT_MEMORY_REPO = "/Users/dorianherle/Documents/Personal/agent_memory";
const PYTHON_BIN = path.join(AGENT_MEMORY_REPO, ".venv/bin/python");
const OTHER_REPO_ENV = "/Users/dorianherle/Documents/Personal/ai_long_task/.env";
const CODEX_NATIVE_BIN =
  "/Users/dorianherle/.npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex";

function loadDotEnv(filePath) {
  const out = {};
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return out;
}

const dotenv = loadDotEnv(OTHER_REPO_ENV);
const GEMINI_API_KEY = dotenv.GEMINI_API_KEY;
if (!GEMINI_API_KEY) {
  throw new Error(`Missing GEMINI_API_KEY in ${OTHER_REPO_ENV}`);
}

function pyMemory(code, extraArgs = []) {
  return execFileSync(PYTHON_BIN, ["-c", code, ...extraArgs], {
    cwd: WORKSPACE,
    env: {
      ...process.env,
      AGENT_MEMORY_PROVIDER: "agent_memory.providers.gemini:GeminiProvider",
      GEMINI_API_KEY,
    },
    encoding: "utf8",
  }).trim();
}

function renderMemoryPacket(question = "") {
  return pyMemory(
    `
import sys
from agent_memory.core import Memory
from agent_memory.prompt import MEMORY_INSTRUCTIONS
m = Memory(${JSON.stringify(WORKSPACE)})
m.write_state_file()
m.write_live_html()
print(m.render_packet(sys.argv[1]))
print()
print(MEMORY_INSTRUCTIONS)
`,
    [question]
  );
}

// Grade any game_logic.js source string against the 3 stage contracts
function gradeStageCode(jsSource) {
  const tmpFile = path.join(os.tmpdir(), `grade-${Date.now()}-${Math.random().toString(36).slice(2)}.cjs`);
  fs.writeFileSync(tmpFile, jsSource, "utf8");
  try {
    const out = execFileSync(
      "node",
      [
        "-e",
        `
        const game = require(${JSON.stringify(tmpFile)});
        const results = {};

        // Stage 1 check: angled shield deflection + brick combo & energy
        {
          const s = game.createState();
          s.ball.x = s.paddle.x + 35;
          s.ball.y = s.paddle.y - s.paddle.h / 2 - s.ball.r + 1;
          s.ball.vx = 0;
          s.ball.vy = 6;
          game.stepBall(s);
          results.angled_shield_bounce = s.ball.vx > 1.5 && s.ball.vy < 0;

          const s2 = game.createState();
          const b0 = s2.bricks[0];
          s2.ball.x = b0.x;
          s2.ball.y = b0.y;
          s2.ball.vx = 0;
          s2.ball.vy = -6;
          game.stepBall(s2);
          results.combo_and_energy_on_brick = (s2.combo === 1 && s2.score === 100 && s2.energy === 35);
        }

        // Stage 2 check: boss rotation + mine spawning/homing + boss armor & core hits
        {
          const s = game.createState();
          s.boss.spawnCooldown = 1;
          game.stepBossAndMines(s);
          results.boss_rotates_and_spawns_mine = (s.boss.angle > 0 && s.mines.length === 1);

          // Armor shatter then core damage
          const s2 = game.createState();
          s2.boss.armor = [true, false, false, false];
          s2.ball.x = s2.boss.x;
          s2.ball.y = s2.boss.y;
          s2.ball.vy = -6;
          game.stepBossAndMines(s2);
          const shatteredFirst = s2.boss.armor.every((a) => !a) && s2.boss.hp === 100;

          s2.ball.x = s2.boss.x;
          s2.ball.y = s2.boss.y;
          s2.ball.vy = -6;
          game.stepBossAndMines(s2);
          const damagedCoreSecond = s2.boss.hp === 80;
          results.boss_armor_and_core_phases = Boolean(shatteredFirst && damagedCoreSecond);
        }

        // Stage 3 check: Perfect Parry + Chrono-Overdrive bullet-time & EMP blast + evaluatePolicy
        {
          const s = game.createState();
          s.paddle.dashTimer = 6;
          s.ball.x = s.paddle.x;
          s.ball.y = s.paddle.y - s.paddle.h / 2 - s.ball.r + 1;
          s.ball.vx = 0;
          s.ball.vy = 6;
          game.stepBall(s);
          results.perfect_parry = (s.combo >= 2 && s.energy >= 45 && s.lastEvent === "perfect_parry");

          const s2 = game.createState();
          s2.combo = 3;
          s2.energy = 60;
          s2.mines = [{ x: 300, y: 200, vx: 0, vy: 2, r: 8, active: true }];
          s2.boss.armor = [true, true, true, true];
          const ok = game.triggerOverdrive(s2);
          results.chrono_overdrive_and_emp = Boolean(
            ok === true &&
            s2.overdriveTimer === 180 &&
            Math.abs(s2.timeScale - 0.3) < 0.01 &&
            s2.mines.length === 0 &&
            s2.boss.armor.filter(Boolean).length === 3
          );

          results.policy_model_fn = (
            game.evaluatePolicy("overdrive", 3, 50, 2) === "activate_emp" &&
            game.evaluatePolicy("overdrive", 1, 50, 2) === "hold" &&
            game.evaluatePolicy("boss_strike", 2, 20, 0) === "damage_core" &&
            game.evaluatePolicy("boss_strike", 2, 20, 2) === "shatter_armor"
          );
        }

        console.log(JSON.stringify(results));
        `,
      ],
      { encoding: "utf8" }
    );
    return JSON.parse(out.trim());
  } catch (err) {
    return { error: String(err.message || err) };
  } finally {
    fs.rmSync(tmpFile, { force: true });
  }
}

async function runFreshCodexAgent({
  iteration,
  agentName,
  cwd,
  withMemory = true,
  taskInstructions,
  question = "",
}) {
  const freshCodexHome = fs.mkdtempSync(path.join(os.tmpdir(), `ralph-game-agent-${iteration}-`));
  const userAuth = path.join(os.homedir(), ".codex", "auth.json");
  fs.copyFileSync(userAuth, path.join(freshCodexHome, "auth.json"));
  fs.chmodSync(path.join(freshCodexHome, "auth.json"), 0o600);

  const memoryPacket = withMemory ? renderMemoryPacket(question) : "";

  const config = {
    memories: {
      generate_memories: false,
      use_memories: false,
    },
  };
  if (withMemory) {
    config.mcp_servers = {
      agent_memory: {
        command: PYTHON_BIN,
        args: ["-m", "agent_memory.cursor.mcp", "--root", cwd],
        default_tools_approval_mode: "approve",
        env: {
          AGENT_MEMORY_PROVIDER: "agent_memory.providers.gemini:GeminiProvider",
          GEMINI_API_KEY,
        },
      },
    };
  }

  const codex = new Codex({
    codexPathOverride: CODEX_NATIVE_BIN,
    env: {
      ...process.env,
      CODEX_HOME: freshCodexHome,
      GEMINI_API_KEY,
      AGENT_MEMORY_PROVIDER: "agent_memory.providers.gemini:GeminiProvider",
    },
    config,
  });

  const thread = codex.startThread({
    model: "gpt-5.6-sol",
    workingDirectory: cwd,
    sandboxMode: "workspace-write",
    skipGitRepoCheck: true,
    approvalPolicy: "never",
    modelReasoningEffort: "medium",
    webSearchEnabled: false,
  });

  const fullPrompt = withMemory
    ? `${memoryPacket}

---
# RALPH LOOP ITERATION ${iteration} — FRESH EMPTY AGENT (${agentName})
You are a brand-new, empty agent instance (${agentName}) with zero prior chat history.
Your ONLY knowledge of prior agents' work, open questions, and user steering comes from \`# MEMORY STATE\` above and the \`agent_memory\` MCP tools (\`memory_find\`, \`memory_save\`, \`memory_summarize\`, \`memory_open_questions\`, \`memory_model\`).
Do NOT pass \`goal=\` to MCP tools (use the default workspace memory). Always pass \`agent="${agentName}"\` when saving/summarizing.

## Objective
${taskInstructions}
`
    : `# RALPH LOOP ITERATION ${iteration} — FRESH EMPTY AGENT WITHOUT MEMORY (${agentName})
You are a brand-new, empty agent instance (${agentName}) with zero prior chat history and NO agent-memory store attached.

## Objective
${taskInstructions}
`;

  console.log(`\n================================================================================`);
  console.log(`🚀 SPAWNING FRESH EMPTY AGENT #${iteration}: ${agentName} (withMemory=${withMemory})`);
  if (withMemory) {
    console.log(memoryPacket.split("\n\nMemory is evidence")[0]);
  } else {
    console.log("   [NO MEMORY STATE INJECTED — AMNESIAC BASELINE]");
  }
  console.log(`================================================================================`);

  const t0 = Date.now();
  const { events } = await thread.runStreamed(fullPrompt);

  const mcpCalls = [];
  const shellCommands = [];
  const fileChanges = [];
  let finalMessage = "";
  let usage = null;
  let threadId = null;

  for await (const event of events) {
    if (event.type === "thread.started") {
      threadId = event.thread_id;
    } else if (event.type === "item.completed") {
      const item = event.item;
      if (item.type === "mcp_tool_call") {
        const resText = item.result?.content?.[0]?.text || JSON.stringify(item.result || item.error);
        mcpCalls.push({
          server: item.server,
          tool: item.tool,
          arguments: item.arguments,
          status: item.status,
          result: resText,
        });
        console.log(`   🧠 [MCP ${item.tool}] args=${JSON.stringify(item.arguments)} -> ${resText.split("\n")[0]}`);
      } else if (item.type === "command_execution") {
        shellCommands.push({ command: item.command, exit_code: item.exit_code });
        console.log(`   💻 [cmd exit=${item.exit_code}] ${item.command}`);
      } else if (item.type === "file_change") {
        fileChanges.push(item.changes);
        console.log(`   📝 [file_change] ${JSON.stringify(item.changes)}`);
      } else if (item.type === "agent_message") {
        finalMessage = item.text;
      }
    } else if (event.type === "turn.completed") {
      usage = event.usage;
    } else if (event.type === "turn.failed") {
      throw new Error(`Turn failed in ${agentName}: ${JSON.stringify(event.error)}`);
    }
  }

  const elapsedSec = Number(((Date.now() - t0) / 1000).toFixed(1));
  fs.rmSync(freshCodexHome, { recursive: true, force: true });
  console.log(`   ✅ ${agentName} finished in ${elapsedSec}s (MCP calls: ${mcpCalls.length}, shell cmds: ${shellCommands.length})`);

  const codeAfter = fs.readFileSync(path.join(cwd, "game_logic.js"), "utf8");
  const checksAfter = gradeStageCode(codeAfter);

  return {
    iteration,
    agentName,
    withMemory,
    threadId,
    elapsedSec,
    injectedMemoryState: withMemory ? memoryPacket.split("\n\nMemory is evidence")[0] : "(No memory packet — amnesiac baseline)",
    mcpCalls,
    shellCommands,
    fileChanges,
    finalMessage,
    usage,
    checksAfter,
    codeSnapshot: codeAfter,
  };
}

async function main() {
  // Clean any old .agent_memory in example/
  fs.rmSync(path.join(WORKSPACE, ".agent_memory"), { recursive: true, force: true });

  const stage0Code = fs.readFileSync(path.join(WORKSPACE, "game_logic.js"), "utf8");
  const stage0Checks = gradeStageCode(stage0Code);

  const goalText =
    "Build ChronoBreaker (game_logic.js): a tactical boss-defense brick-breaker across 3 stages (Stage 1: Curved Shield Reflection & Combo/Energy; Stage 2: Rotating Boss Armor, Weak-Point Core & Homing Mines; Stage 3: User Steering mechanics & Executable Task Model).";

  pyMemory(
    `
import sys
from agent_memory.core import Memory
m = Memory(${JSON.stringify(WORKSPACE)})
m.save_user(sys.argv[1])
m.write_state_file()
m.write_live_html()
`,
    [goalText]
  );

  // --- ITERATION 1: ralph-agent-1 (Fixes Stage 1 + pins exact Stage 2 spec in memory_open_questions) ---
  const iter1 = await runFreshCodexAgent({
    iteration: 1,
    agentName: "ralph-agent-1",
    cwd: WORKSPACE,
    withMemory: true,
    question: "Stage 1 curved shield reflection and brick combo energy",
    taskInstructions: `1. Implement ONLY Stage 1 in \`game_logic.js\` (\`stepBall\`):
   - Curved shield reflection when the ball hits the paddle: compute \`offset = Math.max(-1, Math.min(1, (b.x - p.x) / (p.w / 2)))\` and \`angle = offset * (Math.PI / 3)\`, then set \`b.vx = b.speed * Math.sin(angle)\` and \`b.vy = -b.speed * Math.cos(angle)\`.
   - Brick hit combo & energy: when the ball hits a live brick, increment \`state.combo += 1\`, add \`state.score += 100 * state.combo\`, and increase \`state.energy = Math.min(100, state.energy + 15)\`.
   - Leave \`stepBossAndMines\`, \`triggerOverdrive\`, and \`evaluatePolicy\` untouched for later agents.
2. Verify with \`${PYTHON_BIN} -m pytest -q\`.
3. Hand off to the next empty agent using \`agent_memory\` MCP tools (note: keep the first line of every save under 95 characters so headlines aren't truncated, and put details on following lines!):
   - Call \`memory_save(agent="ralph-agent-1", ...)\` with a <95-char headline and body explaining the Stage 1 fixes in \`stepBall\`.
   - Call \`memory_summarize(agent="ralph-agent-1", ...)\` with a <95-char headline summarizing Stage 1 verification.
   - Call \`memory_open_questions\` to pin the EXACT Stage 2 specification so \`ralph-agent-2\` (which will NOT receive the Stage 2 spec in its prompt!) can implement \`stepBossAndMines(state)\` purely from your memory note:
     * Headline (<95 chars): \`Stage 2 spec: implement stepBossAndMines(state) rotation, homing mines, and boss armor\`
     * Body lines:
       - \`ts = state.timeScale || 1.0; boss.angle += 0.04 * ts; boss.spawnCooldown -= ts;\`
       - \`When boss.spawnCooldown <= 0: reset boss.spawnCooldown = 90 and push mine { x: boss.x, y: boss.y + boss.r, vx: 0, vy: 2.2, r: 8, active: true } to state.mines.\`
       - \`For each active mine: mine.x += Math.sign(p.x - mine.x) * 1.2 * ts; mine.y += mine.vy * ts.\`
       - \`If mine hits paddle (Math.abs(mine.x - p.x) <= p.w/2 + mine.r && Math.abs(mine.y - p.y) <= p.h/2 + mine.r): mine.active = false, state.lives -= 1, state.combo = 0, state.lastEvent = "mine_hit_paddle".\`
       - \`If mine hits ball (Math.hypot(mine.x - b.x, mine.y - b.y) <= mine.r + b.r): mine.active = false, state.score += 50, state.lastEvent = "mine_destroyed". Filter state.mines to keep only mine.active && mine.y <= state.height + 20.\`
       - \`Boss collision with ball (Math.hypot(b.x - boss.x, b.y - boss.y) <= boss.r + b.r): set b.vy = Math.abs(b.speed); if boss.armor has any true entry, set the first true entry to false, state.score += 250, state.lastEvent = "armor_shattered"; else boss.hp = Math.max(0, boss.hp - 20), state.score += 500, state.lastEvent = "boss_core_hit".\``,
  });

  // --- ITERATION 2: ralph-agent-2 (Prompt has NO Stage 2 spec! Must read it from agent-memory!) ---
  const iter2 = await runFreshCodexAgent({
    iteration: 2,
    agentName: "ralph-agent-2",
    cwd: WORKSPACE,
    withMemory: true,
    question: "Stage 2 spec stepBossAndMines rotation homing mines boss armor",
    taskInstructions: `1. You are a brand-new empty agent starting Iteration 2. Notice that your prompt does NOT contain the Stage 2 specification!
2. Inspect \`# MEMORY STATE\` above and call \`memory_find(full=true, ...)\` on the \`## Open questions\` and \`## Handoff\` IDs left by \`ralph-agent-1\` to retrieve the exact specification for \`stepBossAndMines(state)\`.
3. Implement \`stepBossAndMines(state)\` in \`game_logic.js\` according to the specification you retrieved from \`agent-memory\`. Do not touch \`triggerOverdrive\` or \`evaluatePolicy\` yet.
4. Verify your implementation with \`node\` and \`${PYTHON_BIN} -m pytest -q\`.
5. Hand off via \`agent_memory\` MCP tools (keep first line <95 chars):
   - Call \`memory_save(agent="ralph-agent-2", ...)\` recording the verified Stage 2 implementation.
   - Call \`memory_open_questions(ids=[...], text="")\` to close the Stage 2 open question.
   - Call \`memory_summarize(agent="ralph-agent-2", ...)\` to fold Stage 2.`,
  });

  // Save a copy of the Stage 2 workspace for the Amnesiac Agent (without memory) before Iteration 3!
  const noMemDir = fs.mkdtempSync(path.join(os.tmpdir(), "ralph-amnesiac-stage3-"));
  fs.cpSync(path.join(WORKSPACE, "game_logic.js"), path.join(noMemDir, "game_logic.js"));
  fs.cpSync(path.join(WORKSPACE, "pyproject.toml"), path.join(noMemDir, "pyproject.toml"));
  fs.cpSync(path.join(WORKSPACE, "tests"), path.join(noMemDir, "tests"), { recursive: true });

  // --- MID-LOOP USER STEERING (Saved ONLY in .agent_memory, NOT in the prompt or code!) ---
  const steeringSpec = `Steering: Stage 3 Chrono-Overdrive, EMP Shockwave, Perfect Parry & Task Model
1. In stepBall(state) paddle collision: if p.dashTimer > 0 when the ball hits the paddle, trigger Perfect Parry: state.energy = Math.min(100, state.energy + 25), state.combo += 2, b.speed = Math.min(9, b.speed + 0.5) (use this updated b.speed when computing b.vx and b.vy), and set state.lastEvent = "perfect_parry".
2. Implement triggerOverdrive(state): requires state.combo >= 3 and state.energy >= 40 (otherwise return false). When triggered: subtract 40 from state.energy, set state.overdriveTimer = 180, set state.timeScale = 0.3, increment state.empBlasts += 1, clear all active mines (state.mines = []), shatter the first true entry in state.boss.armor (set it to false), set state.lastEvent = "overdrive_emp", and return true.
3. In stepFrame(state, input): after checking input.overdrive, if state.overdriveTimer > 0, decrement state.overdriveTimer -= 1 and keep state.timeScale = state.overdriveTimer > 0 ? 0.3 : 1.0 (when overdriveTimer is 0, state.timeScale must be 1.0).
4. Implement evaluatePolicy(kind, combo, energy, armorCount):
   - when kind == "overdrive": return "activate_emp" if combo >= 3 and energy >= 40, else "hold".
   - when kind == "boss_strike": return "damage_core" if armorCount == 0, else "shatter_armor".
   - otherwise return "none".
5. Register an accepted executable task model via memory_model(operation="handoff", agent="ralph-agent-3") for target "action" with inputs {"kind": "string", "combo": "integer", "energy": "integer", "armorCount": "integer"} and rules/cases matching evaluatePolicy.`;

  pyMemory(
    `
import sys
from agent_memory.core import Memory
m = Memory(${JSON.stringify(WORKSPACE)})
m.save_user(sys.argv[1])
m.write_state_file()
m.write_live_html()
`,
    [steeringSpec]
  );

  // Identical prompt given to both Iteration 3A (WITH agent-memory) and Iteration 3B (WITHOUT agent-memory)!
  const sharedIteration3TaskPrompt = `1. You are a brand-new empty agent starting Iteration 3. Work strictly inside your current working directory (\`.\`) — do not inspect parent directories or \`/tmp\`. Check for any new Stage 3 Steering instructions or Open Questions for \`game_logic.js\` (using \`# MEMORY STATE\` and \`memory_find(full=true)\` if \`agent_memory\` is available, or inspecting the current directory).
2. Implement all pending Stage 3 requirements in \`game_logic.js\` and verify them with \`${PYTHON_BIN} -m pytest -q\` and \`node\`.
3. If \`agent_memory\` MCP tools are available, save your verified findings with \`memory_save\` (first line <95 chars), register the requested executable task model via \`memory_model(operation="handoff")\`, and fold the milestone with \`memory_summarize\`.`;

  // --- ITERATION 3A: ralph-agent-3 (WITH agent-memory) ---
  const iter3WithMemory = await runFreshCodexAgent({
    iteration: 3,
    agentName: "ralph-agent-3",
    cwd: WORKSPACE,
    withMemory: true,
    question: "Stage 3 Chrono-Overdrive EMP Shockwave Perfect Parry evaluatePolicy",
    taskInstructions: sharedIteration3TaskPrompt,
  });

  // --- ITERATION 3B: amnesiac-agent-3 (WITHOUT agent-memory, exact same prompt!) ---
  const iter3WithoutMemory = await runFreshCodexAgent({
    iteration: 3,
    agentName: "amnesiac-agent-3",
    cwd: noMemDir,
    withMemory: false,
    question: "",
    taskInstructions: sharedIteration3TaskPrompt,
  });
  fs.rmSync(noMemDir, { recursive: true, force: true });

  // Export full memory state, history, and accepted task model
  const finalMemory = JSON.parse(
    pyMemory(`
import json
from agent_memory.core import Memory
m = Memory(${JSON.stringify(WORKSPACE)})
m.write_state_file()
live_path = m.write_live_html()
history = [
    {
        "id": item.id[:8],
        "agent": item.payload.get("agent"),
        "user": bool(item.payload.get("user")),
        "open_question": item.payload.get("open_question"),
        "headline": item.message.splitlines()[0][:120],
        "body": item.message,
    }
    for item in m.history()
]
print(json.dumps({
    "final_packet": m.render_packet("ChronoBreaker summary"),
    "live_html": str(live_path),
    "active_model": m.model_get(full=True),
    "history": history,
}, indent=2))
`)
  );

  const bundle = {
    generatedAt: new Date().toISOString(),
    goalText,
    steeringSpec,
    sharedIteration3TaskPrompt,
    stages: {
      stage0: {
        id: "stage0",
        title: "Stage 0: Initial Skeleton (Before Ralph Loop)",
        agent: "initial",
        description: "Flat vertical paddle bounce, no combo multiplier, no Boss AI or mines, no Chrono-Overdrive.",
        checks: stage0Checks,
        code: stage0Code,
      },
      stage1: {
        id: "stage1",
        title: "Stage 1: @ralph-agent-1 (Curved Shield & Combo Core)",
        agent: "ralph-agent-1",
        threadId: iter1.threadId,
        elapsedSec: iter1.elapsedSec,
        description: "Empty Agent #1 fixed angled shield reflection + combo/energy gain, and pinned the Stage 2 Boss spec in memory_open_questions.",
        injectedMemoryState: iter1.injectedMemoryState,
        mcpCalls: iter1.mcpCalls,
        checks: iter1.checksAfter,
        code: iter1.codeSnapshot,
      },
      stage2: {
        id: "stage2",
        title: "Stage 2: @ralph-agent-2 (Boss Armor & Mines via Memory Handoff)",
        agent: "ralph-agent-2",
        threadId: iter2.threadId,
        elapsedSec: iter2.elapsedSec,
        description: "Empty Agent #2 received NO Stage 2 spec in its prompt — it called memory_find() on @ralph-agent-1's open question to build the Boss & Homing Mines!",
        injectedMemoryState: iter2.injectedMemoryState,
        mcpCalls: iter2.mcpCalls,
        checks: iter2.checksAfter,
        code: iter2.codeSnapshot,
      },
      stage3_with_memory: {
        id: "stage3_with_memory",
        title: "Stage 3A: @ralph-agent-3 (WITH Agent Memory — Steering + Task Model)",
        agent: "ralph-agent-3",
        threadId: iter3WithMemory.threadId,
        elapsedSec: iter3WithMemory.elapsedSec,
        description: "Empty Agent #3 read the user's mid-loop ## Steering from agent-memory, built Perfect Parry + 0.3x Chrono-Overdrive + EMP Blast, and registered the executable Task Model.",
        injectedMemoryState: iter3WithMemory.injectedMemoryState,
        mcpCalls: iter3WithMemory.mcpCalls,
        checks: iter3WithMemory.checksAfter,
        code: iter3WithMemory.codeSnapshot,
      },
      stage3_without_memory: {
        id: "stage3_without_memory",
        title: "Stage 3B: @amnesiac-agent-3 (WITHOUT Agent Memory — Same Prompt!)",
        agent: "amnesiac-agent-3",
        threadId: iter3WithoutMemory.threadId,
        elapsedSec: iter3WithoutMemory.elapsedSec,
        description: "Spawned with the EXACT same Iteration 3 prompt, but without agent-memory. Blind to the user's ## Steering note, so Chrono-Overdrive, EMP, and Perfect Parry are missing!",
        injectedMemoryState: iter3WithoutMemory.injectedMemoryState,
        mcpCalls: iter3WithoutMemory.mcpCalls,
        checks: iter3WithoutMemory.checksAfter,
        code: iter3WithoutMemory.codeSnapshot,
      },
    },
    finalMemory,
  };

  fs.writeFileSync(
    path.join(WORKSPACE, "ralph_data.js"),
    `window.RALPH_DATA = ${JSON.stringify(bundle, null, 2)};\n`,
    "utf8"
  );
  console.log("\n🎉 Wrote example/ralph_data.js!");
  console.log("Stage 0 checks:", stage0Checks);
  console.log("Stage 1 checks:", iter1.checksAfter);
  console.log("Stage 2 checks:", iter2.checksAfter);
  console.log("Stage 3A (WITH memory) checks:", iter3WithMemory.checksAfter);
  console.log("Stage 3B (WITHOUT memory) checks:", iter3WithoutMemory.checksAfter);
}

main().catch((err) => {
  console.error("Ralph loop failed:", err);
  process.exit(1);
});
