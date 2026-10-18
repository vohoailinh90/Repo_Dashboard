#!/usr/bin/env python3
"""Record the sample runs of the agent office into index.html, so the Office tab can replay them with no server and no network.

The office lives in claude-agent-routing-template: `scripts/office/snapshot.py` turns a run's events into one snapshot per moment, and its
`scripts/office/demo/` holds two recorded runs (Claude subagents, the VPS pipeline). This script asks that code for the snapshot of each run
every tenth of a second, keeps the first one whole and then only what changed (a patch: objects merge key by key, anything else replaces),
and checks that folding the patches gives back every snapshot it was asked for. The page folds them the same way (`officeAt`) and reads the
result with the reader it uses for a file (`readOffice`).

    python3 scripts/office_sample.py --template ../claude-agent-routing-template           rewrite the block in index.html
    python3 scripts/office_sample.py --template ../claude-agent-routing-template --check   exit 1 if the block differs from what the template makes now

`--template` defaults to $OFFICE_TEMPLATE. Nothing here writes outside index.html, and the template's code is only imported, never changed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
BLOCK_RE = re.compile(r'(<script type="application/json" id="office-sample">\n)(.*?)(\n</script>)', re.S)
RUNS = (("claude", "claude-t3"), ("team", "team-f12"))
STEP = 0.1                       # the events of the samples are stamped to a tenth of a second
CLOCK_KEYS = ("now", "live", "span", "demo")          # set by the page at each moment of the replay, not recorded


def load_snapshot_module(template: Path) -> Any:
    source = template / "scripts" / "office" / "snapshot.py"
    if not source.is_file():
        raise SystemExit(f"{source} not found: point --template (or $OFFICE_TEMPLATE) at a claude-agent-routing-template checkout")
    spec = importlib.util.spec_from_file_location("office_snapshot", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def without_clock(snap: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snap.items() if key not in CLOCK_KEYS}


def diff(old: Any, new: Any) -> Any:
    """What turns `old` into `new` when merged into it. A key the new snapshot no longer has cannot be said, so it is refused."""
    if isinstance(old, dict) and isinstance(new, dict):
        gone = set(old) - set(new)
        if gone:
            raise ValueError(f"a key went away between two moments: {sorted(gone)}")
        return {key: diff(old.get(key), value) for key, value in new.items() if key not in old or old[key] != value}
    return new


def merge(into: Any, patch: Any) -> Any:
    """The page's `officeMerge`: objects merge key by key, anything else (lists, numbers, text, null) replaces."""
    if isinstance(into, dict) and isinstance(patch, dict):
        out = dict(into)
        for key, value in patch.items():
            out[key] = merge(into.get(key), value)
        return out
    return patch


def record(module: Any, name: str) -> dict[str, Any]:
    roster = module.load_roster()
    span = module.load_demo(name)["span"]
    start, end = span["start"], span["end"]
    ticks = int(round((end - start) / STEP))
    # Each moment is asked for in the middle of its tenth, so an event stamped on the tenth is never on the edge of a float comparison.
    moments = [(k * STEP, start + (k + 0.5) * STEP) for k in range(ticks)] + [(round(end - start, 1), end)]
    base, steps, last = None, [], None
    for offset, at in moments:
        snap = without_clock(module.build_demo(name, roster, at))
        if base is None:
            base = last = snap
        elif snap != last:
            steps.append([round(offset, 1), diff(last, snap)])
            last = snap
    run = {"start": start, "end": end, "base": base, "steps": steps}
    for offset, at in moments:                                                    # the replay gives back every snapshot it was made from
        folded = run["base"]
        for t, patch in run["steps"]:
            if t > offset + 1e-9:
                break
            folded = merge(folded, patch)
        if folded != without_clock(module.build_demo(name, roster, at)):
            raise SystemExit(f"{name}: the replay differs from the snapshot at {offset:.1f}s")
    return run


def block(runs: dict[str, Any]) -> str:
    """One run per key, its first snapshot on one line and each patch on its own line, so a change to the sample reads as a small diff."""
    def dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    lines = ['{"version":1,"runs":{']
    for index, (key, run) in enumerate(runs.items()):
        lines.append(f' "{key}":{{"start":{dump(run["start"])},"end":{dump(run["end"])},')
        lines.append(f'  "base":{dump(run["base"])},')
        lines.append('  "steps":[')
        lines.append(",\n".join(f"   {dump(step)}" for step in run["steps"]))
        lines.append("  ]}" + ("," if index < len(runs) - 1 else ""))
    lines.append("}}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", type=Path, default=os.environ.get("OFFICE_TEMPLATE"), help="claude-agent-routing-template checkout (default: $OFFICE_TEMPLATE)")
    parser.add_argument("--check", action="store_true", help="only compare, exit 1 if the page is out of step")
    args = parser.parse_args(argv)
    if not args.template:
        parser.error("--template or $OFFICE_TEMPLATE is needed")
    module = load_snapshot_module(Path(args.template))
    fresh = block({key: record(module, name) for key, name in RUNS})
    html = PAGE.read_text(encoding="utf-8")
    match = BLOCK_RE.search(html)
    if not match:
        raise SystemExit('index.html has no <script type="application/json" id="office-sample"> block')
    if args.check:
        if match.group(2) != fresh:
            print("the office sample in index.html differs from what the template makes now: run python3 scripts/office_sample.py")
            return 1
        print("the office sample is in step")
        return 0
    PAGE.write_text(html[: match.start(2)] + fresh + html[match.end(2):], encoding="utf-8")
    print("index.html " + ("updated" if match.group(2) != fresh else "already in step") + f" ({len(fresh.encode('utf-8')):,} bytes of sample)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
