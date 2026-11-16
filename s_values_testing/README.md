# s_values_testing: choosing V's staleness bounds

V refreshes an agent when **either** backup fires:

| bound | meaning | values swept |
|---|---|---|
| count `s` | more than `s` relevant changes are unseen | 0, 1, 2, 3, 5, inf (off) |
| time `t_max` | a relevant change has waited `t_max` agent calls | off, 1, 2, 3, 5 |

Time is counted in agent calls (wall time divided by the average stage duration), so the same value means the same
thing on a laptop and on a Colab GPU. Relevance favours the multi-agent system's focus files (task files + the files
its agents edit); see `ctxdrift/source_of_truth.py`.

## Run
```bash
python -m s_values_testing.sweep --backend sim --sim-preset small --runs 20     # rough prediction, ~1 min, no GPU
python -m s_values_testing.sweep --backend ollama --mas-model qwen3:4b --modifier-model qwen3:1.7b \
       --runs 2 --s-values 0,1,2 --t-values off,1                                # real models (Colab)
```
`--v-mode lean` (default; also `v`, `hybrid`) picks which V variant is swept. Writes `s_values_testing/results/<timestamp>/`: `sweep.json`, `recommendation.json`, `retained_vs_s.png`,
`pareto.png`, `heatmap_s_t.png`. The regular system and the full re-read run on the same seeds as references, and the
modifier runs in lockstep by default so every setting faces the same sequence of changes (`--parallel` to switch).

## Decision rule
1. Average information retained over the changing scenarios (light, moderate, heavy, churn).
2. Every V setting within `--epsilon` (0.01) of the best counts as "as good".
3. Of those, pick the cheapest: fewest tokens a prefix cache must recompute, then fewest refreshes.

## Simulated prediction (`sim_predictions/`, swept with the plain `v` mode before `lean` existed)
From the behavioural model in `ctxdrift/sim_agent.py`, 20 runs per setting. **These are assumptions turned into
numbers, not measurements**; their job is to choose a sensible grid and to say what to look for on Colab.

| setting | small (~1-2B) | medium (~4-8B) | large | recompute tokens / run |
|---|---|---|---|---|
| V, s=0 | **0.807** | **0.934** | **0.966** | 4.4k |
| V, s=1, t_max=2 | 0.791 | 0.904 | 0.940 | 4.2k |
| full re-read | 0.786 | 0.897 | 0.944 | 13.0k |
| V, s=1, t_max=off | 0.782 | 0.898 | 0.927 | 4.2k |
| regular (no sync) | 0.725 | 0.813 | 0.844 | 3.9k |

- The ranking V (s=0) > full re-read > no sync holds under all three presets.
- Refreshing is cheap because V's updates are small and sit after a stable prefix: s=0 recomputes ~3% more than s=1,
  while a full re-read recomputes ~3x more. So the recommended setting is **s=0 with t_max=1 as the backup** (the
  time bound only matters once s >= 1, where t_max=1 is the best backup at every s).
- V wins most in heavy and churn scenarios. In the moderate scenario with the small preset the full re-read is
  slightly ahead (0.817 vs 0.809), and in the light scenario everything is within noise.
- What the model cannot know: whether frequent updates distract a real small model. If s=0 loses to s=1 or 2 on
  Colab, that is the reason, and it is a result worth reporting.
