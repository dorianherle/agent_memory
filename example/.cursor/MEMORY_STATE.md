# MEMORY STATE

```
## Goal
[6a48c0b6] Build ChronoBreaker (game_logic.js): a tactical boss-defense brick-breaker across 3 stages (Stage 1:…

## Steering
[5e79dc41] Steering: Stage 3 Chrono-Overdrive, EMP Shockwave, Perfect Parry & Task Model
- when kind == "overdrive": return "activate_emp" if combo >= 3 and energy >= 40, else "hold".
- otherwise return "none". … [more]

## Handoff
[ae9c3706] Stage 3 complete: ChronoBreaker overdrive, EMP, perfect parry, and policy verified … [more]

## Task model
[9bb60b75] r2 tested; 6 rules. Read model_get for conditions.
overdrive-ready: Ready overdrive activates the EMP. [action = 'activate_emp' when kind == 'overdrive' and combo >= 3 and energy >= 40]
4 more rules; use model_get.
0 alternatives; 1 unresolved results. … [more]

## Related
[1bb6cb03] [stale] Stage 2 verified: rotating boss armor, homing mines, and core damage … [more]
[2047d068] Stage 2 verified: boss rotation, homing mines, armor shatter, and core damage … [more]
[5454350d] Stage 1 verified: curved paddle reflection and combo-energy brick rewards … [more]
```
