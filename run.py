#!/usr/bin/env python
#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#
"""Observe -> mine -> analyze -> redesign -> agentify, across a database and a web portal.

Run it from the repo root, with the live systems up (see examples/systems)::

    uv run python examples/order_fulfillment/run.py            # stages 1-4, no LLM
    uv run python examples/order_fulfillment/run.py --agent    # + stage 5, live LLM

    1. OBSERVE    a clerk fulfills three mornings of orders: query the order
                  database, book each shipment on the live portal, look the
                  booking up, copy its id back. One log spans both systems.
    2. MINE       the log mines into one process per *order*: the business
                  object both systems carry, found by the correlator.
    3. ANALYZE    handoffs between the systems, the wait on each step, and the
                  values the clerk carried across by hand.
    4. REDESIGN   the look-up step only carries a value the booking already
                  returned, so the to-be plan drops it.
    5. AGENTIFY   (--agent) a live LLM runs the to-be plan on new orders; its
                  shipments and database writes are checked against the rule
                  and its trace against the to-be plan.

Everything the clerk booked is removed from the shared portal at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The sibling modules, made importable by the sys.path line above.
from erp import NEW_BATCH, approve, build_erp, orders_on_disk
from workflow import (
    CASE_TYPE,
    GOAL,
    PORTAL,
    SYSTEMS,
    case_traces,
    expected_net,
    mine,
    observe,
    observed_registry,
    remove_shipments,
    shipments,
    steady_clock,
)

from agenticharvest.core.events import OcelLog
from agenticharvest.domains.sqlite.tools import sqlite_tools
from agenticharvest.domains.web.config import WebConfigError
from agenticharvest.domains.web.tools import web_tools
from agenticharvest.mining import ProcessModel, check_trace
from agenticharvest.mining.analysis import handoffs, performance, retyped_values
from agenticharvest.orchestration.compile import compile_plan, restrict_tools
from agenticharvest.orchestration.redesign import Redesign, plan_model, redesign


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def print_model(model: ProcessModel) -> None:
    """Print what the miner found beyond the variants: frequencies, edges, BPMN.

    Steps are listed in the order the dominant variant takes them (miners
    differ: bupaR sorts alphabetically), so the printout reads as the process.
    """
    dominant = max(model.variants, key=lambda v: v.frequency).sequence if model.variants else []
    rank = {name: i for i, name in enumerate(dominant)}
    print("\n  activities (how often each step happened):")
    for activity in sorted(model.activities, key=lambda a: rank.get(a.name, 99)):
        print(f"    {activity.frequency:3}  {activity.name}")
    print("  directly-follows (which step came straight after which):")
    for edge in sorted(model.directly_follows,
                       key=lambda e: (rank.get(e.source, 99), rank.get(e.target, 99))):
        print(f"    {edge.frequency:3}  {edge.source} -> {edge.target}")
    if model.bpmn is not None:
        tasks = sum(n.type == "task" for n in model.bpmn.nodes)
        print(f"  BPMN: {len(model.bpmn.nodes)} nodes ({tasks} tasks), "
              f"{len(model.bpmn.flows)} sequence flows")
    else:
        print("  BPMN: none (install R and bupaR for BPMN discovery)")


def _system(object_id: str) -> str:
    return object_id.split(":", 1)[0] if object_id.startswith("database:") else object_id


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("order_fulfillment_out"))
    parser.add_argument("--agent", action="store_true", help="run stage 5 with a live LLM")
    parser.add_argument("--keep", action="store_true",
                        help="leave this run's shipments on the portal")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]

    try:
        return _run(args, out, tag)
    except WebConfigError as exc:
        print(f"\nThe shipping portal is not configured: {exc}")
        print("Start the live systems and run examples/systems/setup.py first.")
        return 1
    finally:
        if not args.keep:
            removed = remove_shipments(tag)
            if removed:
                print(f"\n  cleaned up: removed {removed} shipment(s) tagged {tag} from the portal")


def _run(args: argparse.Namespace, out: Path, tag: str) -> int:
    _banner("1. OBSERVE — a clerk fulfills three mornings of orders across two systems")
    log = OcelLog()
    db = build_erp(out / "erp.db")
    for day, booked in observe(db, log, tag=tag).items():
        print(f"  {day:9}  " + ", ".join(f"{o} net {n:.2f}" for o, n in booked.items()))
    (out / "observed.ocel.json").write_text(json.dumps(log.to_ocel2(), indent=2, default=str))
    print(f"\n  {len(log.events)} tool calls, object types: {', '.join(log.object_types())}")
    print(f"  -> {out / 'observed.ocel.json'}")

    _banner("2. MINE — one case per order, across both systems")
    model = mine(log)
    (out / "process_model.json").write_text(model.model_dump_json(indent=2))
    print(f"  miner: {model.miner} · cases: {model.n_cases} · events: {model.n_events}")
    for variant in model.variants:
        print(f"  variant x{variant.frequency}: {' -> '.join(variant.sequence)}")
    print_model(model)
    print(f"\n  -> {out / 'process_model.json'}")

    _banner("3. ANALYZE — handoffs, waits, and values carried by hand")
    for handoff in handoffs(log, CASE_TYPE, SYSTEMS):
        print(f"  handoff  {_system(handoff.source)} -> {_system(handoff.target)}: "
              f"{handoff.count} times, {handoff.cases} orders")
    for edge in performance(log, CASE_TYPE):
        print(f"  wait     {edge.source} -> {edge.target}: median {edge.median_seconds / 60:.0f} min")
    carries = retyped_values(log, CASE_TYPE, SYSTEMS)
    for carry in carries:
        print(f"  carried  {carry.field}: {_system(carry.source)} ({carry.source_activity}) -> "
              f"{_system(carry.target)} ({carry.target_activity}), {carry.cases} orders")

    _banner("4. REDESIGN — drop the steps that only carry data")
    specs = [spec for spec, _ in [*sqlite_tools(), *web_tools()]]
    as_is = compile_plan(model, specs, name="order fulfillment")
    # The only carry that needs a step of its own is the shipment id: the
    # clerk looks the booking up to copy it. The booking response already
    # returns that id, so the look-up goes.
    lookups = {c.source_activity for c in carries if c.source_activity.startswith("call_operation")}
    change = redesign(as_is, remove={a: "re-reads the id the booking call returned" for a in lookups})
    print(change.summary())
    goal = GOAL.format(status=f"booked {tag}")
    prompt = change.to_be.system_prompt(goal)
    (out / "agent_prompt.txt").write_text(prompt)
    print()
    print(prompt)
    print(f"\n  tools granted: {change.to_be.tools}  (of {len(specs)} available)")

    if not args.agent:
        print("\nRe-run with --agent to hand the to-be plan to a live LLM (stage 5).")
        return 0

    _banner("5. AGENTIFY — a live agent runs the to-be plan on new orders")
    from agenticharvest.orchestration.agents.adapters import registry_tools
    from agenticharvest.orchestration.agents.base import AgentConfig
    from agenticharvest.orchestration.agents.factory import AgentFactory, Backend
    from agenticharvest.utils.llm import LLMConfig, LLMConfigError

    try:
        llm = LLMConfig.from_env()
    except LLMConfigError as exc:
        print(f"  no LLM endpoint configured ({exc}); skipping stage 5.")
        return 1

    approve(db, NEW_BATCH)
    agent_log = OcelLog()
    registry = observed_registry(agent_log, clock=steady_clock())
    agent = AgentFactory.create(
        Backend.OPENAI_COMPATIBLE,
        AgentConfig(model=llm.model, system_prompt=prompt, max_tokens=2048, max_retries=30),
        tools=restrict_tools(registry_tools(registry), change.to_be),
        llm_config=llm,
    )

    async def go() -> str:
        try:
            result = await agent.run(
                f"Fulfill the approved orders in the order database at '{db}'. "
                f"The shipping portal is the web app '{PORTAL}'."
            )
        finally:
            await agent.close()
        return result.content

    print(asyncio.run(go()))
    return _check_agent(db, agent_log, change, tag)


def _check_agent(db: Path, agent_log: OcelLog, change: Redesign, tag: str) -> int:
    """Check the agent's work on disk and on the portal, and its trace."""
    want = expected_net(NEW_BATCH)
    booked = {row["order_id"]: float(row["net"]) for row in shipments(tag)
              if row["order_id"] in want}
    on_disk = orders_on_disk(db)
    ids = {str(row["id"]): row["order_id"] for row in shipments(tag)}
    failures = 0
    print("\n  shipments on the portal and orders on disk vs. the pricing rule:")
    for order_id, net in want.items():
        status, ref = on_disk.get(order_id, ("missing", None))
        ok = (booked.get(order_id) == net and status == "shipped"
              and ids.get(str(ref)) == order_id)
        failures += not ok
        print(f"    {'ok ' if ok else 'MISMATCH'} {order_id}: booked net {booked.get(order_id)}"
              f" (rule {net}), status {status}, shipment_ref {ref}")

    to_be = plan_model(change.to_be)
    for case, trace in case_traces(agent_log).items():
        report = check_trace(to_be, trace)
        print(f"\n  {case}: {' -> '.join(trace)}")
        print(f"  conforms to the to-be plan: {report.conforms} · fitness: {report.fitness:.2f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
