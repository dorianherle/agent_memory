(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ChronoBreaker = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  function createState() {
    const bricks = [];
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 8; col++) {
        bricks.push({
          id: `b-${row}-${col}`,
          x: 95 + col * 80,
          y: 135 + row * 28,
          w: 66,
          h: 18,
          hp: 1,
          alive: true,
        });
      }
    }
    return {
      width: 760,
      height: 520,
      tick: 0,
      score: 0,
      combo: 0,
      energy: 20,
      lives: 3,
      overdriveTimer: 0,
      timeScale: 1.0,
      empBlasts: 0,
      lastEvent: "init",
      paddle: { x: 380, y: 470, w: 110, h: 14, vx: 0, speed: 9, dashTimer: 0 },
      ball: { x: 380, y: 440, vx: 0, vy: -6, r: 7, speed: 6 },
      bricks,
      boss: {
        x: 380,
        y: 68,
        r: 34,
        hp: 100,
        maxHp: 100,
        angle: 0,
        armor: [true, true, true, true],
        spawnCooldown: 90,
      },
      mines: [],
    };
  }

  function stepPaddle(state, input = {}) {
    const p = state.paddle;
    const dir = (input.right ? 1 : 0) - (input.left ? 1 : 0);
    if (input.dash && p.dashTimer <= 0) {
      p.dashTimer = 12;
    }
    const mult = p.dashTimer > 0 ? 1.6 : 1.0;
    p.vx = dir * p.speed * mult;
    p.x = Math.max(p.w / 2, Math.min(state.width - p.w / 2, p.x + p.vx));
    if (p.dashTimer > 0) p.dashTimer -= 1;
  }

  function stepBall(state) {
    const b = state.ball;
    const p = state.paddle;
    b.x += b.vx;
    b.y += b.vy;

    if (b.x - b.r <= 0 || b.x + b.r >= state.width) b.vx = -b.vx;
    if (b.y - b.r <= 0) b.vy = Math.abs(b.vy);

    if (
      b.vy > 0 &&
      b.y + b.r >= p.y - p.h / 2 &&
      b.y - b.r <= p.y + p.h / 2 &&
      Math.abs(b.x - p.x) <= p.w / 2 + b.r
    ) {
      const offset = Math.max(-1, Math.min(1, (b.x - p.x) / (p.w / 2)));
      const angle = offset * (Math.PI / 3);
      if (p.dashTimer > 0) {
        state.energy = Math.min(100, state.energy + 25);
        state.combo += 2;
        b.speed = Math.min(9, b.speed + 0.5);
      }
      b.vx = b.speed * Math.sin(angle);
      b.vy = -b.speed * Math.cos(angle);
      state.lastEvent = p.dashTimer > 0 ? "perfect_parry" : "paddle_hit";
    }

    for (const brick of state.bricks) {
      if (!brick.alive) continue;
      if (
        b.x + b.r >= brick.x - brick.w / 2 &&
        b.x - b.r <= brick.x + brick.w / 2 &&
        b.y + b.r >= brick.y - brick.h / 2 &&
        b.y - b.r <= brick.y + brick.h / 2
      ) {
        brick.alive = false;
        b.vy = -b.vy;
        state.combo += 1;
        state.score += 100 * state.combo;
        state.energy = Math.min(100, state.energy + 15);
        state.lastEvent = "brick_hit";
        break;
      }
    }

    if (b.y - b.r > state.height) {
      state.lives -= 1;
      state.combo = 0;
      b.x = p.x;
      b.y = p.y - 24;
      b.vx = 0;
      b.vy = -b.speed;
      state.lastEvent = "ball_lost";
    }
  }

  function stepBossAndMines(state) {
    const boss = state.boss;
    const p = state.paddle;
    const b = state.ball;
    const ts = state.timeScale || 1.0;

    boss.angle += 0.04 * ts;
    boss.spawnCooldown -= ts;
    if (boss.spawnCooldown <= 0) {
      boss.spawnCooldown = 90;
      state.mines.push({
        x: boss.x,
        y: boss.y + boss.r,
        vx: 0,
        vy: 2.2,
        r: 8,
        active: true,
      });
    }

    for (const mine of state.mines) {
      if (!mine.active) continue;

      mine.x += Math.sign(p.x - mine.x) * 1.2 * ts;
      mine.y += mine.vy * ts;

      if (
        Math.abs(mine.x - p.x) <= p.w / 2 + mine.r &&
        Math.abs(mine.y - p.y) <= p.h / 2 + mine.r
      ) {
        mine.active = false;
        state.lives -= 1;
        state.combo = 0;
        state.lastEvent = "mine_hit_paddle";
      }

      if (Math.hypot(mine.x - b.x, mine.y - b.y) <= mine.r + b.r) {
        mine.active = false;
        state.score += 50;
        state.lastEvent = "mine_destroyed";
      }
    }

    state.mines = state.mines.filter(
      (mine) => mine.active && mine.y <= state.height + 20
    );

    if (Math.hypot(b.x - boss.x, b.y - boss.y) <= boss.r + b.r) {
      b.vy = Math.abs(b.speed);
      const armorIndex = boss.armor.findIndex(Boolean);
      if (armorIndex !== -1) {
        boss.armor[armorIndex] = false;
        state.score += 250;
        state.lastEvent = "armor_shattered";
      } else {
        boss.hp = Math.max(0, boss.hp - 20);
        state.score += 500;
        state.lastEvent = "boss_core_hit";
      }
    }
  }

  function triggerOverdrive(state) {
    if (state.combo < 3 || state.energy < 40) return false;

    state.energy -= 40;
    state.overdriveTimer = 180;
    state.timeScale = 0.3;
    state.empBlasts += 1;
    state.mines = [];

    const armorIndex = state.boss.armor.findIndex(Boolean);
    if (armorIndex !== -1) state.boss.armor[armorIndex] = false;

    state.lastEvent = "overdrive_emp";
    return true;
  }

  function evaluatePolicy(kind, combo, energy, armorCount) {
    if (kind === "overdrive") {
      return combo >= 3 && energy >= 40 ? "activate_emp" : "hold";
    }
    if (kind === "boss_strike") {
      return armorCount === 0 ? "damage_core" : "shatter_armor";
    }
    return "none";
  }

  function stepFrame(state, input = {}) {
    state.tick += 1;
    if (input.overdrive) {
      triggerOverdrive(state);
    }
    if (state.overdriveTimer > 0) {
      state.overdriveTimer -= 1;
      state.timeScale = state.overdriveTimer > 0 ? 0.3 : 1.0;
    } else {
      state.timeScale = 1.0;
    }
    stepPaddle(state, input);
    stepBall(state);
    stepBossAndMines(state);
    return state;
  }

  return {
    createState,
    stepPaddle,
    stepBall,
    stepBossAndMines,
    triggerOverdrive,
    evaluatePolicy,
    stepFrame,
  };
});
