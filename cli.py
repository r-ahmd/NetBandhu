"""The CLI: one chat turn per line, and the two commands a paused run needs.

    uv run python cli.py                      # the demo subscriber, SUB-2041
    uv run python cli.py --as SUB-2077        # another subscriber
    uv run python cli.py --thread demo-1      # a named conversation

    /pending                     show a paused approval, if there is one
    /approve                     resume the pause with "approved"
    /edit 250                    resume with a reduced amount
    /reject first offence        resume, and the subscriber is told this reason
    /quit

WHY THE RESUME COMMANDS ARE IN THE CLI
    E2 is the claim that a pause survives the process. The only honest way to show it
    is to stop the program, start it again, and resume from the checkpoint — which
    means resume cannot live inside the same running process. So it is a command
    line, and the thread id is what carries the state across the restart.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from langgraph.types import Command

from core.config import settings
from middleware import SubscriberContext
from tools import backend
from workflows.graph import open_graph


def banner(subscriber_id: str, thread: str) -> None:
    rec = backend()["subscribers"].get(subscriber_id, {})
    print(f"NetBandhu · {settings.APP_NAME} v{settings.VERSION}")
    print(f"  logged in as {rec.get('name', subscriber_id)} ({subscriber_id})")
    print(f"  node {rec.get('node', '?')} · thread {thread!r} · model {settings.model_id}")
    print("  /pending, /approve, /edit <amount>, /reject <reason>, /quit")
    print()


def show_pause(graph, config) -> bool:
    """Print a pending approval. Returns True if the run is paused."""
    snap = graph.get_state(config)
    pending = [v for v in (snap.values or {}).get("__interrupt__", [])] or []
    interrupts = getattr(snap, "interrupts", ()) or ()
    if not snap.next and not interrupts:
        return False
    if interrupts:
        payload = interrupts[0].value
        print("\n  ⏸  PAUSED — a billing supervisor must decide")
        for k, v in (payload.get("proposal") or {}).items():
            print(f"     {k:11s} {v}")
        print(f"     options     {', '.join(payload.get('your_options', []))}")
        print("  Use /approve, /edit <amount> or /reject <reason>.\n")
    return True


def turn(graph, config, text: str, subscriber_id: str, now: str = "") -> None:
    """One turn. `subscriber_id` is RUNTIME IDENTITY, passed with the input.

    It is never derived from the text: a subscriber who types a different id is
    still themselves, and `authorize()` compares tool calls against this value.
    """
    for event in graph.stream({"message": text, "subscriber_id": subscriber_id,
                               "actor": "subscriber", "now": now},
                              config=config,
                              stream_mode="updates"):
        for node, delta in event.items():
            if node == "__interrupt__":
                continue
            for line in (delta or {}).get("progress", []) or []:
                print(f"   · {line}")
            if (delta or {}).get("answer"):
                print(f"\n{delta['answer']}\n")


def resume(graph, config, decision: dict) -> None:
    for event in graph.stream(Command(resume=decision), config=config,
                              stream_mode="updates"):
        for node, delta in event.items():
            if node == "__interrupt__":
                continue
            for line in (delta or {}).get("progress", []) or []:
                print(f"   · {line}")
            if (delta or {}).get("answer"):
                print(f"\n{delta['answer']}\n")
    out = graph.get_state(config).values.get("outcome")
    if out:
        print(f"   outcome: {json.dumps(out)[:160]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NetBandhu support desk")
    ap.add_argument("--as", dest="subscriber", default="SUB-2041",
                    help="subscriber id to log in as (default SUB-2041)")
    ap.add_argument("--thread", default="demo-1", help="conversation thread id")
    ap.add_argument("--say", default="", help="run one turn and exit")
    ap.add_argument("--fresh", action="store_true",
                    help="clear the checkpoint database first (a clean demo)")
    ap.add_argument("--now", default="",
                    help="ISO clock for a LIVE incident, e.g. 2026-06-14T14:00:00 "
                         "(empty uses the incident's ETA — a demo needs a fixed clock)")
    args = ap.parse_args(argv)

    if args.fresh:
        settings.ensure_dirs()
        if settings.CHECKPOINT_DB.exists():
            settings.CHECKPOINT_DB.unlink()
        print("(thread store cleared)")
    config = {"configurable": {"thread_id": args.thread}}
    banner(args.subscriber, args.thread)

    with open_graph() as graph:
        if args.say:
            turn(graph, config, args.say, args.subscriber, args.now)
            show_pause(graph, config)
            return 0

        show_pause(graph, config)
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line in ("/quit", "/exit"):
                return 0
            if line == "/pending":
                if not show_pause(graph, config):
                    print("   nothing is paused\n")
                continue
            if line == "/approve":
                resume(graph, config, {"action": "approve"})
                continue
            if line.startswith("/edit"):
                amount = line.split(maxsplit=1)[1] if len(line.split()) > 1 else ""
                try:
                    resume(graph, config, {"action": "edit", "amount": float(amount)})
                except ValueError:
                    print("   usage: /edit 250\n")
                continue
            if line.startswith("/reject"):
                reason = line[len("/reject"):].strip() or "no reason given"
                resume(graph, config, {"action": "reject", "reason": reason})
                continue
            turn(graph, config, line, args.subscriber, args.now)
            show_pause(graph, config)


if __name__ == "__main__":
    raise SystemExit(main())
