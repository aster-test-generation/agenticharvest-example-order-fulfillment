#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""The order database: a real SQLite file the clerk queries and updates.

    customers   id, name
    orders      id (O-…), customer_id, qty, unit_price, status, shipment_ref
                status: approved -> shipped

The ``mark_shipped`` trigger stamps ``shipped_at`` when an order's status
becomes ``shipped``, so the database keeps its own record of the last step.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE orders (
    id           TEXT PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    qty          INTEGER NOT NULL,
    unit_price   REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'approved',
    shipment_ref TEXT,
    shipped_at   TEXT
);
CREATE TRIGGER mark_shipped AFTER UPDATE OF status ON orders
WHEN NEW.status = 'shipped'
BEGIN
    UPDATE orders SET shipped_at = datetime('now') WHERE id = NEW.id;
END;
"""

CUSTOMERS = [(1, "Acme Tools"), (2, "Bolt & Nut"), (3, "Crane Supply")]

#: Each batch is one morning's approved orders: (id, customer id, qty, unit price).
BATCHES: dict[str, list[tuple[str, int, int, float]]] = {
    "monday": [("O-1001", 1, 12, 9.50), ("O-1002", 2, 60, 3.00)],
    "tuesday": [("O-1003", 3, 25, 4.00), ("O-1004", 1, 5, 20.00)],
    "wednesday": [("O-1005", 2, 18, 7.25), ("O-1006", 3, 50, 2.40)],
}

#: Orders the agent processes, never seen while observing.
NEW_BATCH = [("O-2001", 1, 30, 5.00), ("O-2002", 3, 8, 12.50)]

#: (minimum qty, discount rate), highest tier first.
TIERS: list[tuple[int, float]] = [(50, 0.15), (20, 0.10), (10, 0.05), (0, 0.0)]


def net_amount(qty: float, unit_price: float) -> float:
    """The pricing rule: qty x unit price, less the first tier the qty meets."""
    rate = next(rate for minimum, rate in TIERS if qty >= minimum)
    return round(qty * unit_price * (1 - rate), 2)


def build_erp(dest: str | Path) -> Path:
    """Create an empty order database (customers only) at ``dest``."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    connection = sqlite3.connect(dest)
    try:
        connection.executescript(SCHEMA)
        connection.executemany("INSERT INTO customers VALUES (?, ?)", CUSTOMERS)
        connection.commit()
    finally:
        connection.close()
    return dest


def approve(db: Path, batch: list[tuple[str, int, int, float]]) -> None:
    """Add ``batch`` to the database as newly approved orders (sales did this)."""
    connection = sqlite3.connect(db)
    try:
        connection.executemany(
            "INSERT INTO orders (id, customer_id, qty, unit_price) VALUES (?, ?, ?, ?)", batch
        )
        connection.commit()
    finally:
        connection.close()


def orders_on_disk(db: Path) -> dict[str, tuple[str, str | None]]:
    """Order id -> (status, shipment_ref), read back from the file."""
    connection = sqlite3.connect(db)
    try:
        return {
            order_id: (status, ref)
            for order_id, status, ref in connection.execute(
                "SELECT id, status, shipment_ref FROM orders"
            )
        }
    finally:
        connection.close()
