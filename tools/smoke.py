"""Developer smoke check: run the demo pipeline in-process and print key numbers."""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import Config, Runtime, dumps  # noqa: E402

steps = int(sys.argv[1]) if len(sys.argv) > 1 else 30
t0 = time.time()
rt = Runtime(Config(mode="demo", start_feeds=False, cache_dir=""))
rt.demo.bootstrap()
print("bootstrap incl. history", round(time.time() - t0, 2), "s", rt.store.history)
ready_seen = Counter()
leader_ready = 0
worst = 0.0
for i in range(steps):
    rt.demo.step()
    t1 = time.time()
    snap = rt.scanner.build_snapshot()
    worst = max(worst, time.time() - t1)
    for s, r in snap["records"].items():
        if r.get("obState") == "READY":
            ready_seen[s] += 1
    leader_ready += sum(1 for side in ("bullish", "bearish") for s in snap["leaders"][side]
                        if snap["records"][s].get("obState") == "READY")
print("steps", steps, "worst snapshot", round(worst, 3), "s  market time", snap["marketTime"])
print("READY symbol-steps", sum(ready_seen.values()), "distinct", len(ready_seen), "in-leader READY", leader_ready)
u = snap["universe"]
print("universe", u["available"], u["bullish"], u["bearish"])
for side in ("bullish", "bearish"):
    print(side, [(s, snap["records"][s]["mom"], snap["records"][s]["obQualityScore"], snap["records"][s]["obState"])
                 for s in snap["leaders"][side]])
    print(" picks", [(p.get("symbol"), p.get("obQualityScore"), p.get("pickStatus")) for p in snap["picks"][side]["slots"]],
          "pending", snap["picks"][side]["pending"])
print("ob states", Counter(r.get("obState") for r in snap["records"].values()))
for tf in ("5m", "15m", "30m", "1h"):
    d = rt.scanner.symbol_detail("CDSL", tf)
    print(tf, "candles", len(d["candles"]), "zones", len(d["zones"]), "ob", d["ob"]["obQualityScore"], d["ob"]["obState"])
print("snapshot bytes", len(dumps(snap)))
