[![make.fun](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Absaa00/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Absaa00/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

# GTA VI launch-day Twitch CCU quantile forecaster

Entry for the [make.fun bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
"Forecast GTA VI's launch-day Twitch viewers hour by hour" ($250, top 20).

`main.py` (Python 3.13, standard library only) reads every `*.in` case in
the input directory (schema `makefun/twitch-ccu-case/v1`) and writes one
`<id>.out` forecast per case: five quantiles (5/25/50/75/95%) of the Twitch
category's hourly viewer count for release-day hours 2–24.

## Method

Each past launch in the case's `comparables` contributes its release-day
hourly curve as log-ratios to its own hour-1 print. Per hour, the forecast
is the weighted empirical quantiles of those log-ratios, scaled back by the
case's hour-1 print. Weights combine:

- **scale** — a Gaussian kernel (bandwidth 1.0 in log space) on hour-1
  print similarity, so big launches shape the forecast of a big launch;
- **unlock pattern** — launches that unlocked at rolling midnight local
  time (like GTA VI) count double, because they ride the same timezone
  unlock wave (New Zealand → Europe → UK → US East → US West);
- **floor** — a small 0.05 weight on every comparable keeps the pool from
  collapsing onto a couple of launches.

Hours with too few comparables fall back to the house baseline shape (flat
median, spread `0.15 * sqrt(h - 1)`). Deterministic, no network, no clock,
no randomness — about 0.5 s for the four practice cases.

## Local validation

Leave-one-out over the kit's 37 usable comparables, scored with the exact
pinball loss from the rules:

| set | this forecaster | house baseline |
| --- | --- | --- |
| all 37 launches | 0.466 | 0.930 |
| 20 launches with hour-1 ≥ 50k (GTA VI's size class) | 0.202 | 0.251 |

90% coverage on the big-launch set: 0.89.

## Run locally

```sh
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## License

MIT — see [LICENSE](LICENSE).
