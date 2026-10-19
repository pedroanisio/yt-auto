#!/usr/bin/env python3
"""Worked examples of driving Claude Code from Python.

Run a single section:   python example_usage.py reason
Run them all:           python example_usage.py all
"""

from __future__ import annotations

import json
import sys

from claude_engine import (
    ClaudeEngine, EngineConfig, Task, TaskRunner,
    READ_ONLY_TOOLS, FULL_TOOLS,
)


def demo_reason() -> None:
    """1. Pure reasoning — no tools, no filesystem access, fastest path."""
    engine = ClaudeEngine(EngineConfig(tools=(), model="sonnet"))
    answer = engine.ask(
        "A train leaves at 14:05 and the trip takes 3h50m. "
        "What time does it arrive? Answer with the time only."
    )
    print("arrival:", answer.text)
    print("cost:", engine.usage)


def demo_structured() -> None:
    """2. Structured reasoning — the schema is enforced by the CLI."""
    engine = ClaudeEngine(EngineConfig(tools=(), model="sonnet"))
    schema = {
        "type": "object",
        "properties": {
            "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            "confidence": {"type": "number"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sentiment", "confidence", "reasons"],
    }
    data = engine.ask_json(
        "Classify the sentiment of this support ticket: "
        "'The install worked but the docs were wrong twice and I lost an hour.'",
        schema,
    )
    print(json.dumps(data, indent=2))


def demo_agentic() -> None:
    """3. Agentic work — Claude uses tools; we watch each step."""
    engine = ClaudeEngine(EngineConfig(
        tools=list(READ_ONLY_TOOLS),
        permission_mode="dontAsk",
        cwd=".",
    ))
    for event in engine.stream(
        "List the Python files in this directory and summarise what each one does "
        "in a single line. Use your Glob and Read tools."
    ):
        if event.subtype == "tool_use":
            print(f"  [tool] {event.tool_name} {json.dumps(event.tool_input)[:100]}")
        elif event.type == "assistant" and event.text:
            print(event.text)
    print("cost:", engine.usage)


def demo_conversation() -> None:
    """4. Multi-turn — resume=True keeps the same session and its context."""
    engine = ClaudeEngine(EngineConfig(tools=(), model="sonnet"))
    print(engine.ask("Pick a prime number between 50 and 100. Reply with the number only.").text)
    print(engine.ask("Is the number you picked also a sum of two squares? One line.",
                     resume=True).text)


def demo_pipeline() -> None:
    """5. A dependency-ordered pipeline; later tasks see earlier outputs."""
    engine = ClaudeEngine(EngineConfig(tools=(), model="sonnet"))

    tasks = [
        Task(
            name="ideas",
            prompt="Give 3 ideas for automating a weekly ops report. JSON only.",
            schema={
                "type": "object",
                "properties": {
                    "ideas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"title": {"type": "string"},
                                           "effort": {"type": "string"}},
                            "required": ["title", "effort"],
                        },
                    }
                },
                "required": ["ideas"],
            },
            check=lambda out: len(out["ideas"]) == 3,
            retries=2,
        ),
        Task(
            name="pick",
            depends_on=["ideas"],
            prompt=("From these ideas:\n{ideas}\n\n"
                    "Pick the best effort-to-value one and justify it in 2 sentences."),
        ),
        Task(
            name="plan",
            depends_on=["pick"],
            prompt="Turn this into a 5-step implementation checklist:\n{pick}",
        ),
    ]

    runner = TaskRunner(engine, on_progress=lambda n, m: print(f"[{n}] {m}"))
    results = runner.run(tasks)
    for name, result in results.items():
        print(f"\n=== {name} (ok={result.ok}) ===")
        print(result.output if isinstance(result.output, str)
              else json.dumps(result.output, indent=2))
    print("\ntotal:", engine.usage)


DEMOS = {
    "reason": demo_reason,
    "structured": demo_structured,
    "agentic": demo_agentic,
    "conversation": demo_conversation,
    "pipeline": demo_pipeline,
}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "reason"
    targets = DEMOS.values() if which == "all" else [DEMOS[which]]
    for demo in targets:
        print(f"\n{'=' * 70}\n{demo.__doc__}\n{'=' * 70}")
        demo()
