# Eval bench

Golden footage with known-good outputs. Playbook or code changes run this bench before they merge, so the engine cannot silently get worse.

Planned structure (fills in as M1-M5 land):

```
eval/
  golden/<clip>/         short reference recordings (10-60s, checked in or fetched)
    expected/            blessed artifacts: srt cues, silence map, cut lists
  run_bench.py           re-analyzes golden clips, diffs against expected/
```

First golden clips to bless once M1 output is human-verified:
- a talky segment (caption + filler rules)
- a silent build segment (scene/visual rules)
- a segment with music (loudness + speech/music separation)
