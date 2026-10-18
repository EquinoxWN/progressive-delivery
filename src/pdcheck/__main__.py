"""Command line: python -m pdcheck validate | schedule | judge."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .checks import check_all, expected_weights, fetch_crd, judge, load, read_observed, seconds


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch; exit 1 when a check fails."""
    p = argparse.ArgumentParser(prog="pdcheck", description=__doc__)
    p.add_argument("--config", type=Path, default=Path("config"))
    p.add_argument("--cache", type=Path, default=Path(".tmp"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="Flagger schema, workload rules and cross-references")
    sub.add_parser("schedule", help="print the canary's expected traffic steps")
    j = sub.add_parser("judge", help="compare an observed rollout (JSON lines) with the schedule")
    j.add_argument("observed", type=Path)
    a = p.parse_args(argv)
    canaries = [d for d in load(a.config) if d.kind == "Canary"]
    if a.cmd == "validate":
        errs = check_all(a.config, fetch_crd(a.cache))
        for e in errs:
            print("violation:", e)
        print(f"validated {len(load(a.config))} objects against Flagger {canaries and 'v1.45.0'} schema and house rules: {len(errs)} violation(s)")
        return 1 if errs else 0
    if a.cmd == "schedule":
        for c in canaries:
            an = c.body["spec"]["analysis"]
            w = expected_weights(an)
            step = seconds(an["interval"])
            print(f"Canary {c.namespace}/{c.name}: canary weights {w} every {an['interval']}; promotion after about {step * (len(w) + 1)} s")
        return 0
    result = judge(read_observed(a.observed), canaries[0])
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
