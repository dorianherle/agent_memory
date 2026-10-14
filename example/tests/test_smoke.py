import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str) -> dict:
    out = subprocess.check_output(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
    )
    return json.loads(out.strip())


def test_module_loads_and_steps():
    res = run_node(
        """
        const game = require('./game_logic.js');
        const state = game.createState();
        game.stepFrame(state, { right: true });
        console.log(JSON.stringify({ tick: state.tick, x: state.paddle.x, bricks: state.bricks.length }));
        """
    )
    assert res["tick"] == 1
    assert res["x"] > 380
    assert res["bricks"] == 24
