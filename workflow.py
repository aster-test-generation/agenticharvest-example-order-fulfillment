#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Order fulfillment across two systems: an order database and a shipping portal.

Shared by ``run.py`` and the live test that keeps it honest. Every call goes to
a real system: a SQLite order database and the shipping portal served by
Directus (see ``examples/systems``).

The clerk's process, every morning (one case per order):

    1. pull the approved orders            query:orders                         (database)
    2. book a shipment on the portal       send_operation:createItemsShipments  (portal)
    3. look the booking up again           call_operation:readItemsShipments    (portal)
    4. copy its id back, mark it shipped   execute:orders                       (database)

The two systems share no id, so the case is the business object both carry:
the order id. A :class:`~agenticharvest.core.events.Correlator` finds it in SQL rows,
SQL arguments, request bodies, and response bodies, and relates every event to
it. Analysis then shows the handoffs and the values carried by hand, and the
redesign drops step 3: the booking call already returned the shipment id.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from erp import BATCHES, approve, net_amount

from agenticharvest.core.events import (
    CompositeMapper,
    Correlator,
    KeyRule,
    OcelLog,
    OcelTap,
    ValueRule,
)
from agenticharvest.core.tools import InMemoryTap, ToolRegistry
from agenticharvest.domains.sqlite.ocel import OBJ_DATABASE, SqliteOCEL
from agenticharvest.domains.sqlite.tools import sqlite_tools
from agenticharvest.domains.web import WebOCEL
from agenticharvest.domains.web.ocel import OBJ_WEBAPP
from agenticharvest.domains.web.tools import client_for, web_tools
from agenticharvest.gateway.dispatch import ToolRequest, dispatch
from agenticharvest.mining import (
    BupaRMiner,
    Miner,
    ProcessModel,
    VariantMiner,
    collapse_repeats,
    traces,
)

#: The case: one per order.
CASE_TYPE = "order"

#: The object types that are systems, for handoff and carry analysis.
SYSTEMS = (OBJ_DATABASE, OBJ_WEBAPP)

#: The configured web app the shipping portal is (see examples/systems).
PORTAL = "directus"

#: The order id's shape.
ORDER_ID = r"\bO-\d{4}\b"

GOAL = (
    "You fulfill the approved orders in an order database. For each approved "
    "order, compute its net amount: qty x unit_price, less 15% when qty >= 50, "
    "10% when qty >= 20, 5% when qty >= 10, rounded to cents. Book one shipment "
    "for it on the shipping portal with order_id, customer (the customer's name), "
    "net, and status '{status}'. Then record it in the database with: UPDATE orders "
    "SET status = 'shipped', shipment_ref = ? WHERE id = ? — the shipment_ref is "
    "the id of the shipment the portal created."
)

KEY_RULES = [
    # Order ids in SQL results and arguments.
    KeyRule(object_type=CASE_TYPE, tools=["query"], result=["rows"], pattern=ORDER_ID),
    KeyRule(object_type=CASE_TYPE, tools=["execute"], param="args", pattern=ORDER_ID),
    KeyRule(object_type=CASE_TYPE, tools=["execute"], param="sql", pattern=ORDER_ID),
    # Order ids in portal requests and responses.
    KeyRule(object_type=CASE_TYPE, tools=["send_operation"], param="body.order_id"),
    KeyRule(object_type=CASE_TYPE, tools=["call_operation"],
            param="query.filter.order_id._eq"),
    KeyRule(object_type=CASE_TYPE, tools=["call_operation"],
            result=["body.data.*.order_id"]),
]

VALUE_RULES = [
    # What the clerk reads out of each system...
    ValueRule(field="row", direction="read", tools=["query"], result=["rows.*.*"]),
    ValueRule(field="shipment", direction="read", tools=["call_operation"],
              result=["body.data.*.id"]),
    # ...and types into the other.
    ValueRule(field="order_id", direction="write", tools=["send_operation"],
              param="body.order_id"),
    ValueRule(field="customer", direction="write", tools=["send_operation"],
              param="body.customer"),
    ValueRule(field="shipment_ref", direction="write", tools=["execute"], param="args.0"),
]


def tool_domains() -> dict[str, str]:
    """Tool name -> the domain that serves it."""
    domains = {spec.name: "sqlite" for spec, _ in sqlite_tools()}
    domains.update({spec.name: "web" for spec, _ in web_tools()})
    return domains


def correlator() -> Correlator:
    """The mapper that relates every call to its system and to the orders it names."""
    inner = CompositeMapper(
        {"sqlite": SqliteOCEL(qualify_tables=True), "web": WebOCEL(qualify_operations=True)},
        tool_domains(),
    )
    return Correlator(inner, KEY_RULES, values=VALUE_RULES)


def observed_registry(
    log: OcelLog,
    clock: Callable[[], datetime] | None = None,
    records: InMemoryTap | None = None,
) -> ToolRegistry:
    """One registry over both systems, every call recorded into ``log``."""
    taps: list[Any] = [OcelTap(mapper=correlator(), log=log, clock=clock)]
    if records is not None:
        taps.append(records)
    registry = ToolRegistry(taps=taps)
    for spec, handler in [*sqlite_tools(), *web_tools()]:
        registry.register(spec, handler)
    return registry


def steady_clock(start: datetime | None = None, minutes: int = 3) -> Callable[[], datetime]:
    """A clock that ticks ``minutes`` per call, so waits are measurable."""
    ticks: Iterator[int] = iter(range(10**9))
    origin = start or datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    return lambda: origin + timedelta(minutes=minutes * next(ticks))


def _call(registry: ToolRegistry, tool: str, **params: Any) -> Any:
    result = dispatch(registry, ToolRequest(tool=tool, params=params))
    if not result.ok:
        raise RuntimeError(f"{tool} failed: {result.error}")
    return result.output


def fulfill_by_hand(registry: ToolRegistry, db: Path, *, status: str) -> dict[str, float]:
    """Play the clerk for one morning, through the real tools.

    Args:
        registry: The observed registry.
        db: The order database.
        status: The status booked on each shipment. It carries a per-run tag so
            a run's rows can be found and removed from the shared portal.

    Returns:
        Order id -> the net amount booked on the portal.
    """
    orders = _call(
        registry, "query", path=str(db),
        sql="SELECT o.id, c.name, o.qty, o.unit_price FROM orders o "
            "JOIN customers c ON c.id = o.customer_id WHERE o.status = 'approved' "
            "ORDER BY o.id",
    )["rows"]
    booked: dict[str, float] = {}
    for order_id, customer, qty, price in orders:
        net = net_amount(qty, price)
        _call(registry, "send_operation", app=PORTAL, name="createItemsShipments",
              body={"order_id": order_id, "customer": customer, "net": net, "status": status},
              confirm=True)
        # The clerk finds the booking on the portal to copy its id back.
        found = _call(registry, "call_operation", app=PORTAL, name="readItemsShipments",
                      query={"filter": {"order_id": {"_eq": order_id}}})
        shipment_id = found["body"]["data"][-1]["id"]
        _call(registry, "execute", path=str(db),
              sql="UPDATE orders SET status = 'shipped', shipment_ref = ? WHERE id = ?",
              args=[str(shipment_id), order_id], confirm=True)
        booked[order_id] = net
    return booked


def observe(
    db: Path, log: OcelLog, *, tag: str, records: InMemoryTap | None = None
) -> dict[str, dict[str, float]]:
    """Stage 1: observe the clerk across every morning's batch.

    Sales approves each morning's orders into the one order database; the clerk
    then fulfills them.
    """
    registry = observed_registry(log, clock=steady_clock(), records=records)
    booked = {}
    for day, batch in BATCHES.items():
        approve(db, batch)
        booked[day] = fulfill_by_hand(registry, db, status=booked_status(tag))
    return booked


def booked_status(tag: str) -> str:
    """The shipment status a run books, carrying its tag."""
    return f"booked {tag}"


def expected_net(batch: list[tuple[str, int, int, float]]) -> dict[str, float]:
    """Order id -> the net the pricing rule gives, for checking any run."""
    return {order_id: net_amount(qty, price) for order_id, _c, qty, price in batch}


def default_miner() -> Miner:
    """bupaR (which adds a BPMN) when R is installed, else the variant miner."""
    bupar = BupaRMiner(case_type=CASE_TYPE)
    return bupar if bupar.available() else VariantMiner(case_type=CASE_TYPE)


def mine(log: OcelLog, miner: Miner | None = None) -> ProcessModel:
    """Stage 2: mine one case per order, folding repeated steps."""
    return (miner or default_miner()).mine(collapse_repeats(log, case_type=CASE_TYPE))


def case_traces(log: OcelLog) -> dict[str, list[str]]:
    """Each order's trace, with repeated steps collapsed."""
    return traces(collapse_repeats(log, case_type=CASE_TYPE), case_type=CASE_TYPE)


def shipments(tag: str) -> list[dict[str, Any]]:
    """The portal's shipments from one run, read live."""
    reply = client_for(PORTAL).request(
        "get", "/items/shipments",
        query={"filter": {"status": {"_eq": booked_status(tag)}}, "limit": -1},
    )
    return list((reply.body or {}).get("data", []))


def remove_shipments(tag: str) -> int:
    """Delete one run's shipments from the shared portal; return how many."""
    rows = shipments(tag)
    if rows:
        client_for(PORTAL).request("delete", "/items/shipments", body=[row["id"] for row in rows])
    return len(rows)
