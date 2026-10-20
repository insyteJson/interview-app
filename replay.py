"""Replay a list of participant answers through the real classifier and the engine, offline.

Use it before sending links: feed in the answers from earlier interviews (or invented test answers)
and check that every question the app would show follows the protocol. Nothing is written to the database.

    export ANTHROPIC_API_KEY=sk-ant-...
    python replay.py answers.txt            # one answer per paragraph (separated by a blank line)

The first paragraph is the role form, one entry per line ("category; title; company or club; from; to"):

    ROLES
    job; IT team lead; a startup; 01/2024; now
    hobby; Competitive ballroom dancer; TSC Example
    python replay.py answers.txt --model claude-sonnet-5-5
"""

from __future__ import annotations

import argparse
import json
import os

import engine
import roles
from classifier import Classifier


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("answers_file")
    ap.add_argument("--model", default=os.environ.get("MODEL", "claude-opus-5-5"))
    ap.add_argument("--effort", default=os.environ.get("EFFORT", "medium"),
                    choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--out", default="replay-log.json")
    args = ap.parse_args()

    with open(args.answers_file, encoding="utf-8") as f:
        answers = [a.strip() for a in f.read().split("\n\n") if a.strip()]

    clf = Classifier(os.environ["ANTHROPIC_API_KEY"], args.model, args.effort)
    state, res = engine.start(engine.new_state("REPLAY", args.model))
    print("Q0:", res["output"], "\n")
    if not answers or not answers[0].upper().startswith("ROLES"):
        raise SystemExit("The first paragraph must be the ROLES block (see the docstring at the top of replay.py).")
    positions, errors = roles.normalise(roles.parse_roles_block(answers.pop(0)))
    if errors:
        raise SystemExit("ROLES block: " + " ".join(errors))
    state, res = engine.set_positions(state, positions)
    print("FORM:\n" + roles.summary(state["positions"]))
    print(f"Q{state['turn']} [{res['rule']}]: {res['output']}\n")
    log = []
    for a in answers:
        if state["status"] in ("complete", "aborted"):
            print("[session ended; remaining answers ignored]")
            break
        cls, meta = clf.classify(state, a)
        phase = state["phase"]
        state, res = engine.step(state, a, cls)
        print(f"A{state['turn']} [{phase}] -> {cls['label']} ({cls.get('rationale')})")
        print(f"   tokens in/out: {meta.get('input_tokens')}/{meta.get('output_tokens')} "
              f"| {meta.get('latency_s')} s")
        print(f"   {a[:160]}{'...' if len(a) > 160 else ''}")
        print(f"Q{state['turn']} [{res['rule']}]: {res['output']}\n")
        log.append({"turn": state["turn"], "phase": phase, "answer": a, "classification": cls,
                    "rule": res["rule"], "question": res["output"], "meta": meta})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"log": log, "final_state": state}, f, ensure_ascii=False, indent=2)
    tin = sum((e["meta"].get("input_tokens") or 0) for e in log)
    tout = sum((e["meta"].get("output_tokens") or 0) for e in log)
    cost = tin * 4 / 1e6 + tout * 20 / 1e6
    print(f"Tokens in/out: {tin}/{tout} for {len(log)} answers "
          f"(about ${cost:.2f} at Opus 5.5 list prices of $4 / $20 per million, ignoring caching discounts)")
    print(f"Log written to {args.out}")


if __name__ == "__main__":
    main()
