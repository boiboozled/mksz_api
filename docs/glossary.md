# Advanced statistics glossary

Definitions of the columns returned by `BoxScoreAdvanced`, `LeagueDashTeamStats` and
`LeagueDashPlayerStats`. The selection and naming follow the
[NBA stats glossary](https://www.nba.com/stats/help/glossary) where a handball equivalent exists. The
per-possession scale follows the EHF, which reports stats per 50 possessions (about one team's
possessions in a match).

**Conventions**

- Every `*_PCT` column is a percentage from 0 to 100, rounded to one decimal.
- **Ratings are per 50 possessions.**
- "Per 60" means per 60 minutes: on-court minutes for players, game time for teams.
- Season values are computed from **summed counts**. For example, a season `SHOT_PCT` is total goals
  divided by total shots. It is never the average of the per-match percentages.

## Data sources

| Source | What it gives | Coverage |
|---|---|---|
| **Report** (`BoxScore`) | Official counts per player: goals, shots, 7 m, saves, turnovers, technical faults, cards, time on court | Every played match |
| **Play-by-play** (`PlayByPlay`) | Every shot with its court zone and goal cell (blocked shots included), turnovers, sanctions, timeouts | Matches with a livescore log |
| **Lineup feed** (in the play-by-play) | Starters, substitutions, suspension leave and return | NB I-level matches; not in lower leagues such as NB II |

For NB I, the play-by-play rebuilds the report exactly: every player's goals, shots by outcome, 7 m
throws, turnovers, faults, cards, saves and shots faced. The lineup feed also gives each player's time on
court to the second.

Possession-based columns use only matches that have a play-by-play. `PBP_GAMES` says how many of a
row's games that is; when it is lower than `GP`, the other matches still count in the report-based
columns. Lineup-based columns (on/off, plus-minus, usage) also need the lineup feed (`LINEUP_GAMES`).

**Data quality**

- **Lineup feeds can be corrupted.** Some feeds repeat the starters or substitute players who are not on
  court. A feed is therefore used only if each team has the right number of players on court (7 minus
  active suspensions) at least 95% of the time. Otherwise the match falls back to suspensions mode, and
  `BoxScoreAdvanced` reports `LINEUPS_REJECTED` with the error share in `LINEUP_ERROR_PCT`.
  - In NB I 2025/26, 162 of 184 feeds pass. In 154 of those, every player's rebuilt time on court equals
    the report to the second.
  - Where they differ, the report is sometimes the one that is wrong. For example, one match's report
    counts no second-half minutes for seven players who kept playing.
- **Suspensions** last 120 s, and are counted from the leave event.
- **Scorer logs** occasionally miss events. A rare match with an incomplete log shows up as a large gap
  between `POSS` and `OPP_POSS`, which is normally 0–1 per match.

## Possessions

A **possession** ends with a terminal event of the attacking team. Terminal events are:
- a goal;
- any other shot outcome: saved, missed, post or blocked (7 m throws included);
- a turnover (lost ball);
- a technical fault.

The attacking team **keeps the ball** if the next terminal event in the same period is also theirs and
follows a non-goal shot. That happens after a rebound, or after a block that goes out of bounds. The
possession then continues, and its `RETAINED` count goes up by one. This is the handball equivalent of
an offensive rebound. A new half always starts a new possession.

The play-by-play does not record possessions directly; this is the reconstruction. In practice the two
teams' possession counts differ by at most one in a match.

`BoxScoreAdvanced.possessions` lists every possession. Its columns:
- Who and when: `OFFENSE`, `START_SEC`, `END_SEC`, `DURATION`.
- How it ended: `END_TYPE`, `GOALS`, `ATTEMPTS`, `RETAINED`, `IS_7M`, `SHOT_ZONE_GROUP`.
- The score: `SCORE_MARGIN_START`, `SCORE_MARGIN_END`.
- The numerical situation: `OFF_FIELD_PLAYERS`, `DEF_FIELD_PLAYERS`, `SITUATION`, `OFF_EMPTY_NET`, `DEF_EMPTY_NET`.
- Context flags: `IS_CLUTCH`, `AFTER_TIMEOUT`.
- Who was on court: `OFF_LINEUP`, `DEF_LINEUP`.

The state of the game is taken at the possession's last event.

## Team: `measure_type="Advanced"`

| Column | Definition |
|---|---|
| `POSS`, `OPP_POSS` | Possessions of the team and of its opponents |
| `PACE` | Possessions per team per 60 minutes: ((`POSS` + `OPP_POSS`) / 2) × 3600 / seconds played |
| `AVG_POSS_SEC` | Average possession length in seconds. A high value means slow, patient attacks (a proxy for passive play) |
| `ORTG` | Offensive rating: goals per 50 possessions = 50 × goals / `POSS` |
| `DRTG` | Defensive rating: goals conceded per 50 opponent possessions |
| `NET_RTG` | `ORTG` − `DRTG` |
| `ATTACK_EFF_PCT` | Attack efficiency, the classic handball measure: goals / possessions (= `ORTG` / 50, as a percentage) |
| `ATTEMPTS` | Shot attempts, **blocked shots included** (the report's `SHOTS` leaves them out) |
| `ATT_GOAL_PCT` | Goals / attempts (blocked shots included) |
| `NON7M_PCT` | Goal % of field shots (7 m throws excluded), from the report |
| `SEVEN_M_PCT` | 7 m goals / 7 m throws |
| `TOV_PCT` | (Turnovers + technical faults) / possessions |
| `LOST_BALL_PCT`, `TECH_FAULT_PCT` | The two parts of `TOV_PCT` |
| `BLOCKED_PCT` | The team's attempts that were blocked |
| `OPP_BLOCKED_PCT` | Opponent attempts the team blocked: a defensive block rate |
| `SAVE_PCT` | The team's saves / shots on target faced (goals + saves) |
| `OPP_SAVE_PCT` | Opponent goalkeepers' save % against this team |
| `SUSPENSIONS`, `SUSPENSIONS_DRAWN` | 2-minute suspensions of the team, including bench officials, and of its opponents |

## Team: `measure_type="FourFactors"`

Dean Oliver's four factors, adapted to handball. Each also has an `OPP_` version, which is the same
measure for the team's opponents, i.e. its defence.

| Column | Basketball analogue | Definition |
|---|---|---|
| `ATT_GOAL_PCT` | eFG% | Goals / attempts, blocked shots included |
| `TOV_PCT` | TOV% | (Turnovers + technical faults) / possessions |
| `RETAIN_PCT` | OREB% | Retained possessions / non-goal shot ends: how often the team kept the ball after a save, miss, post or block |
| `SEVEN_M_RATE` | FT rate | 7 m throws / possessions |

## Team: `measure_type="ShotProfile"`

For each zone group `G`, there are four columns:
- `G_ATT`: attempts from the group.
- `G_GOALS`: goals from the group.
- `G_PCT`: goals / attempts.
- `G_SHARE`: the group's share of the team's attempts.

There is also `FAST_BREAK_GOALS_PER_50`.

| Group | Court zones (see the README's zone table) |
|---|---|
| `WING` | 0, 4 |
| `BACK_9M` | 1, 2, 3 |
| `LONG_RANGE` | 5, 6, 7 |
| `PIVOT` | 8, 9, 10 |
| `BREAKTHROUGH` | 11, 12, 13 |
| `FAST_BREAK` | 15 |
| `SEVEN_M` | 17 (7 m throws) |
| `OTHER` | 14 (trick shot), 16 (own half) |
| `UNKNOWN` | No zone recorded by the scorer |

## Team: `measure_type="Situations"`

The table has one row per team and situation. Situations are seen from the team's side and come from
active suspensions, not from the goalkeeper:

| `SITUATION` | Meaning |
|---|---|
| `EVEN` | Both teams have the same number of players suspended |
| `ADVANTAGE` | The opponent has more players suspended (power play) |
| `DISADVANTAGE` | The team has more players suspended (short-handed) |
| `EMPTY_NET` | The team's own goalkeeper is off court, replaced by a 7th field player. This row overlaps the other three |

Columns:
- `MIN`: time spent in the situation.
- `POSS`, `GOALS` and `ORTG`: the team's attacking possessions in the situation, its goals, and its rating.
- `OPP_POSS`, `OPP_GOALS`, `DRTG`: the same while defending in the situation. For `EMPTY_NET`, `OPP_GOALS` means goals conceded into the empty goal.
- `NET_RTG`, `GOAL_DIFF`.

In the `EMPTY_NET` row, `POSS` counts attacks played with a 7th field player.

`STRENGTH_SOURCE` says where the state came from:
- `lineups`: from the lineup feed. Suspensions come from the suspension leave and return events, and the
  empty net from the goalkeeper (by the report's GK flag) not being on court.
- `suspensions`: the match has no lineup feed. Field players are 6 − active suspensions, where a
  `TWO_MINUTES` event counts as 120 s if leave and return events are missing. The empty net comes from
  the scorer's "goalkeeper: none" events, which are less precise.

## Team: `measure_type="GameFlow"`

| Column | Definition |
|---|---|
| `LEAD_CHANGES` | Times the lead passed from one team to the other (ties in between allowed) |
| `TIMES_TIED` | Goals after which the score was level (0–0 not counted) |
| `LARGEST_LEAD` | The team's biggest lead. Over a season this is the best match, not a sum |
| `LONGEST_RUN` | The most consecutive goals by the team. Over a season this is the best match |
| `TIME_LEADING_MIN`, `TIME_TRAILING_MIN`, `TIME_TIED_MIN` | Minutes spent ahead, behind and level |
| `CLUTCH_POSS`, `CLUTCH_GOALS`, `CLUTCH_OPP_POSS`, `CLUTCH_OPP_GOALS`, `CLUTCH_ORTG`, `CLUTCH_DRTG`, `CLUTCH_NET` | The same stats in clutch time only |
| `POST_TIMEOUT_POSS`, `POST_TIMEOUT_GOALS`, `POST_TIMEOUT_EFF_PCT` | The team's first possession after each of its own timeouts, and how often it scored. Compare with `ATTACK_EFF_PCT` |

**Clutch time** covers the last 5 minutes of the second half, plus all of overtime, while the margin is
within 2 goals. A possession counts as clutch when its last event happens in clutch time.

Every endpoint takes `clutch=True`, which computes *all* columns from clutch time only. Box-score counts
then come from the play-by-play, because the report cannot be split by time.

## Players: `measure_type="Advanced"`

| Column | Definition |
|---|---|
| `POSS_ON`, `OFF_POSS_ON`, `DEF_POSS_ON` | Team possessions (attack + defence, attack, defence) that ended while the player was on court |
| `ATTEMPTS`, `ATT_GOAL_PCT` | Shot attempts (blocked shots included) and goals / attempts |
| `USG_PCT` | Usage: the player's plays / the team's plays while the player is on court. A play is a shot attempt, a turnover or a technical fault. The on-court players' usage adds up to 100%, so the average is about 1/6 |
| `TOV_RATIO` | (Turnovers + technical faults) / plays |
| `GOAL_SHARE`, `SHOT_SHARE` | The player's share of the team's goals and attempts in the matches they played |
| `PLUS_MINUS` | Team goals − opponent goals while the player was on court |
| `ON_ORTG`, `ON_DRTG`, `ON_NET` | Team ratings in possessions with the player on court |
| `OFF_ORTG`, `OFF_DRTG`, `OFF_NET` | Team ratings with the player off court (in the same matches) |
| `ON_OFF_NET` | `ON_NET` − `OFF_NET` |
| `ADVANTAGE_GOALS`, `DISADVANTAGE_GOALS`, `EMPTY_NET_GOALS`, `CLUTCH_GOALS` | Goals scored on a power play, short-handed, with the team's own goal empty (7 v 6 attack), and in clutch time |
| `G_SHARE`, `G_PCT` | The shot profile by zone group, as in ShotProfile |

## Players: `measure_type="Goalkeeping"`

| Column | Definition |
|---|---|
| `SAVES`, `SHOTS_FACED`, `SAVE_PCT` | From the report. Shots faced = goals + saves against the goalkeeper; blocked and missed shots are excluded |
| `SAVES_7M`, `SHOTS_FACED_7M`, `SEVEN_M_SAVE_PCT` | 7 m throws faced and saved |
| `G_FACED`, `G_SAVE_PCT` | Shots on target faced and save % for each zone group |
| `SAVES_PER_60` | Saves per 60 minutes on court |
| `DEF_POSS_ON`, `ON_DRTG` | Opponent possessions with the goalkeeper on court, and goals conceded per 50 of them |

## Per modes

`per_mode` scales **counting** columns only; rates and percentages never change.

| `per_mode` | Counting columns divided by |
|---|---|
| `Totals` | Nothing (season totals) |
| `PerGame` | Games played (`MIN` becomes minutes per game) |
| `Per60` | Minutes / 60. For players these are on-court minutes, like NBA per-36; for situations, the time spent in the situation |
| `Per50Possessions` | Possessions / 50, where possessions = (attack + defence) / 2. Players use their on-court possessions; goalkeepers use the defensive possessions they were on court for |

## Not available

The data has no assists, steals, blocks credited to a defender, rebounds, or drawn fouls and 7 m
throws per player. Stats that need these (assist rate, defensive ratings per player, xG with shot
context, ...) cannot be computed.
