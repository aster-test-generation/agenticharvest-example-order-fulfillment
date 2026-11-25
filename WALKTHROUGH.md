# Order fulfillment: the full walkthrough

This page walks through the whole example, stage by stage: what it sets up,
what each step does, the output you will see, the files it writes, and why each
stage matters. Every output shown here was copied from a real run against the
live systems. Ids, tags, and wait times will differ in your run; the shape and
the results will not.

For a short overview, see [README.md](README.md).

**Contents**

1. [The problem](#1-the-problem)
2. [What runs where](#2-what-runs-where)
3. [Set up and run](#3-set-up-and-run)
4. [Stage 1: Observe](#4-stage-1-observe)
5. [Stage 2: Mine](#5-stage-2-mine)
6. [Stage 3: Analyze](#6-stage-3-analyze)
7. [Stage 4: Redesign](#7-stage-4-redesign)
8. [Stage 5: Agentify](#8-stage-5-agentify)
9. [Cleanup](#9-cleanup)
10. [The output files](#10-the-output-files)
11. [Look at it yourself](#11-look-at-it-yourself)
12. [What the safety checks do](#12-what-the-safety-checks-do)
13. [Change it](#13-change-it)
14. [Troubleshooting](#14-troubleshooting)
15. [How the code is organized](#15-how-the-code-is-organized)

---

## 1. The problem

A clerk fulfills approved orders every morning. Two systems are involved:

- **The order database** (SQLite). Sales has already approved the orders here.
  Each order has an id like `O-1001`, a customer, a quantity, and a unit price.
- **The shipping portal** (Directus, a real web app). Every shipment is booked
  here.

For each order, the clerk:

1. Looks up the approved orders in the database.
2. Works out the net amount with the discount rule, and books a shipment on
   the portal.
3. Finds the new booking on the portal, to copy down the shipment id it was
   given.
4. Goes back to the database, marks the order shipped, and types in that
   shipment id.

The discount rule:

| Quantity | Discount |
|---|---|
| 50 or more | 15% |
| 20 to 49 | 10% |
| 10 to 19 | 5% |
| under 10 | none |

`net = qty × unit_price × (1 − discount)`, rounded to cents.

**Why it is hard to see this process.** The two systems share no id. The
database knows `O-1001`; the portal knows shipment `194`. The database's log
shows step 1 and step 4. The portal's log shows step 2 and step 3. Neither log
shows the process, and neither shows that step 3 exists only to carry a number
back by hand.

This example joins the two systems into one log, mines the process out of it,
finds the steps that only carry data between systems, removes one, and has an
AI agent run the shorter process. Then it checks the agent's work in both
systems.

## 2. What runs where

```
  your machine
  ┌──────────────────────────────────────────────────────────────────────┐
  │                                                                      │
  │   run.py  (stages 1-5)                                               │
  │     │                                                                │
  │     │  every call goes through one AgenticHarvest tool registry            │
  │     ▼                                                                │
  │   ┌─────────────────────────┐        ┌────────────────────────────┐  │
  │   │ SQLite tools            │        │ web tools                  │  │
  │   │ query · execute · …     │        │ call_operation ·           │  │
  │   └───────────┬─────────────┘        │ send_operation · …         │  │
  │               │                      └──────────────┬─────────────┘  │
  │               ▼                                     ▼                │
  │   order_fulfillment_out/erp.db          Directus (container, :8055)  │
  │   a real SQLite file                    the shipping portal          │
  │                                                                      │
  │   the tap records every call ──▶ one OCEL event log, keyed by order  │
  └──────────────────────────────────────────────────────────────────────┘
```

- **The database** is a real SQLite file, created fresh in the output folder
  on each run. It has a `customers` table, an `orders` table, and a
  `mark_shipped` trigger that stamps `shipped_at` when an order's status
  becomes `shipped`.
- **The portal** is a real Directus server, running in a container. Its
  `shipments` collection has the fields `order_id`, `customer`, `net`, `status`,
  and `date_created`. AgenticHarvest did not hand-write any of its tools: it reads
  the portal's live OpenAPI description (`/server/specs/oas`) and builds
  `createItemsShipments`, `readItemsShipments`, and the other operations from
  it.
- **The agent** (stage 5) is any OpenAI-compatible LLM endpoint you configure.

## 3. Set up and run

There are two ways. Both give the same output.

### A. Containers only (no Python on your machine)

You need [Podman](https://podman.io/docs/installation) or Docker, git, and
about 4 GB of free memory.

```bash
git clone https://github.com/aster-test-generation/agenticharvest-example-order-fulfillment.git
cd agenticharvest-example-order-fulfillment
./run.sh                 # Windows: .\run.ps1
```

On the first run, `run.sh`:

1. Finds Podman (or Docker) and checks it is running.
2. Fetches AgenticHarvest into `./.agenticharvest` (once), with this example and
   the live stack as submodules. The run uses that copy of the example, not the
   files in this clone.
3. Starts Directus, Keycloak, and Odoo in containers. The first start pulls
   about 2 GB of images and takes a few minutes. Later runs take seconds.
4. Builds the **runner** image: Python with AgenticHarvest and this example installed.
5. Provisions the apps: creates the `shipments` collection and an API token
   (`setup.py`).
6. Runs `run.py` inside the runner.

You will see this before the example's own output:

```
Starting the live systems with podman (the first start pulls ~2 GB and takes a few minutes)...
...
Provisioning the live systems (examples/systems/setup.py)...
Checking each app through AgenticHarvest:
  directus   ok      Dynamic API Specification …: 2 resources, 14 operations (10 write)
  keycloak   ok      Keycloak Admin REST API 1.0: 50 resources, 369 operations (181 write)
  odoo       ok      Odoo 19.0-…: 5 resources, 51 operations (41 write)
```

Those three lines mean AgenticHarvest reached each app and built its tools from the
app's own description. This example only uses Directus.

### B. With Python and uv (for developers)

From the AgenticHarvest repo root, cloned with `--recurse-submodules`:

```bash
uv sync --extra web --extra openai_compatible --extra dev
podman compose -f examples/systems/compose.yaml up -d
uv run python examples/systems/setup.py
uv run python examples/order_fulfillment/run.py
```

`setup.py` writes the portal's address and token to `.env` in the repo root.
Run scripts from the repo root, so they find that file.

### Options

| Option | What it does |
|---|---|
| *(none)* | Stages 1 to 4. No LLM needed. Takes under a second once the stack is up. |
| `--agent` | Adds stage 5: a live LLM runs the redesigned process. Takes 10 to 30 seconds. |
| `--keep` | Leaves this run's shipments on the portal, so you can look at them. |
| `--out DIR` | Where the output files go (host runs). Default: `order_fulfillment_out/`. With `run.sh`, files go to `out/order_fulfillment/` next to it; set `AGENTICHARVEST_OUT` to change that. |

For `--agent`, set an OpenAI-compatible endpoint (OpenAI, a LiteLLM proxy,
vLLM, or a local server) in the environment or in `.env`:

```bash
API_BASE=https://your-endpoint/v1
API_KEY=your-key
MODEL=your-model
```

In the containers-only setup, put those three lines in a `.env` file next to
`run.sh`.

---

## 4. Stage 1: Observe

**What happens.** The script plays the clerk for three mornings. Each morning,
sales approves two new orders into the database, and the clerk fulfills them,
using exactly the steps from [section 1](#1-the-problem). Every step is a real
tool call: SQL against the SQLite file, and HTTP requests to the Directus
server.

All calls go through **one tool registry** that holds both the SQLite tools
and the web tools. The registry has a **tap**: a hook that sees every call
after it runs and records it as an event in an **OCEL** log (Object-Centric
Event Log, an open standard for process-mining data).

**Output:**

```
========================================================================
1. OBSERVE — a clerk fulfills three mornings of orders across two systems
========================================================================
  monday     O-1001 net 108.30, O-1002 net 153.00
  tuesday    O-1003 net 90.00, O-1004 net 100.00
  wednesday  O-1005 net 123.97, O-1006 net 102.00

  21 tool calls, object types: database, table, order, webapp, operation, resource
  -> order_fulfillment_out/observed.ocel.json
```

(Paths in the output are where *this* run wrote its files. See
[section 10](#10-the-output-files) for where yours go.)

**Reading it.**

- Each line is one morning and the net amount booked for each order. For
  example, `O-1002` is 60 units at 3.00, so 15% off: 60 × 3.00 × 0.85 = 153.00.
- **21 tool calls.** Each morning makes one `query` that returns both of its
  orders, then three calls per order (book, look up, update): 3 mornings × (1
  + 2 × 3) = 21.
- **Object types.** Each event relates to the things it touched:
  - `database`, `table`: the SQLite file and its `orders` table.
  - `webapp`, `operation`, `resource`: the portal, the operation called
    (`createItemsShipments`), and the collection it acted on (`shipments`).
  - **`order`**: the business object. No system gave the log this; the
    correlator found it (below).

### How one log covers two systems

Two pieces make this work, both in `workflow.py`:

**1. `CompositeMapper`** sends each call to the right domain's mapper. A
`query` goes to the SQLite mapper, which records the database and the table.
A `send_operation` goes to the web mapper, which records the web app, the
operation, and the resource.

**2. `Correlator`** looks inside each call for the order id and relates the
event to that order. Its rules say where the id appears in each kind of call:

```python
KEY_RULES = [
    # In SQL results and SQL arguments.
    KeyRule(object_type="order", tools=["query"],   result=["rows"], pattern=r"\bO-\d{4}\b"),
    KeyRule(object_type="order", tools=["execute"], param="args",    pattern=r"\bO-\d{4}\b"),
    KeyRule(object_type="order", tools=["execute"], param="sql",     pattern=r"\bO-\d{4}\b"),
    # In portal request bodies, query filters, and responses.
    KeyRule(object_type="order", tools=["send_operation"], param="body.order_id"),
    KeyRule(object_type="order", tools=["call_operation"], param="query.filter.order_id._eq"),
    KeyRule(object_type="order", tools=["call_operation"], result=["body.data.*.order_id"]),
]
```

A path like `body.data.*.order_id` walks into the JSON: `*` means "every item
of this list". So the last rule finds every order id in a portal listing.

Here are the four events for order `O-1001`, from `observed.ocel.json`
(trimmed). Look at the `relationships`: every event, in either system, names
`order:O-1001`.

```json
{ "id": "e1", "type": "query:orders", "time": "2026-10-05T08:00:00Z",
  "relationships": [
    { "objectId": "database:…/erp.db",        "qualifier": "target" },
    { "objectId": "table:…/erp.db:orders",    "qualifier": "reads" },
    { "objectId": "order:O-1001",             "qualifier": "reads" },
    { "objectId": "order:O-1002",             "qualifier": "reads" } ] }

{ "id": "e2", "type": "send_operation:createItemsShipments", "time": "2026-10-05T08:03:00Z",
  "attributes": [ { "name": "records", "value": ["194"] }, { "name": "status", "value": 200 } ],
  "relationships": [
    { "objectId": "webapp:directus",                        "qualifier": "target" },
    { "objectId": "operation:directus:createItemsShipments", "qualifier": "invokes" },
    { "objectId": "resource:directus:shipments",            "qualifier": "writes" },
    { "objectId": "order:O-1001",                           "qualifier": "writes" } ] }

{ "id": "e3", "type": "call_operation:readItemsShipments", "time": "2026-10-05T08:06:00Z",
  "relationships": [ …, { "objectId": "order:O-1001", "qualifier": "reads" } ] }

{ "id": "e4", "type": "execute:orders", "time": "2026-10-05T08:09:00Z",
  "relationships": [ …, { "objectId": "order:O-1001", "qualifier": "writes" } ] }
```

- The query event relates to **both** orders, because it returned both.
- A call made with `confirm=true` (a write) relates as `writes`; any other call
  relates as `reads`.
- `records: ["194"]` is the shipment id the portal returned. The web mapper
  records the ids each call addressed or returned.

**Timestamps.** The script uses a steady clock that ticks 3 minutes per call,
so the waits in stage 3 are readable. Without it, every call would land within
the same second. In a real deployment, the tap uses the real time.

### If you don't know the join key

Here, we knew to look for `O-1234`. When you don't, record a session with an
`InMemoryTap` and ask `suggest_keys` which values appear in more than one
system:

```python
from agenticharvest.core.events import suggest_keys
suggest_keys(records.records, correlator(), artifact_types=["database", "webapp"])
```

On this example it returns one suggestion, which is the order id:

```
pattern \bO\-\d+\b   examples ['O-1001', 'O-1002', 'O-1003']   systems database, webapp   in 24 events
```

---

## 5. Stage 2: Mine

**What happens.** The log is mined with **`order` as the case**: each order is
one run of the process, and its events, in time order, are its trace. Runs of
the same step on one order are folded into one (`collapse_repeats`), so an
order with several identical writes still counts as one step.

**Which miner runs.** If R and the bupaR packages are installed, the example
uses **bupaR**, which also discovers a BPMN model. Otherwise it uses the
pure-Python **variant miner**, which finds the same activities, edges, and
variants, without a BPMN. The runner container has no R, so it always uses the
variant miner. Both give the same variants on this log.

**Output** (with bupaR):

```
========================================================================
2. MINE — one case per order, across both systems
========================================================================
  miner: bupaR · cases: 6 · events: 24
  variant x6: query:orders -> send_operation:createItemsShipments -> call_operation:readItemsShipments -> execute:orders

  activities (how often each step happened):
      6  query:orders
      6  send_operation:createItemsShipments
      6  call_operation:readItemsShipments
      6  execute:orders
  directly-follows (which step came straight after which):
      6  query:orders -> send_operation:createItemsShipments
      6  send_operation:createItemsShipments -> call_operation:readItemsShipments
      6  call_operation:readItemsShipments -> execute:orders
  BPMN: 4 nodes (4 tasks), 5 sequence flows

  -> order_fulfillment_out/process_model.json
```

With the variant miner, the first line says `miner: variants` and the last
model line says `BPMN: none (install R and bupaR for BPMN discovery)`.
Everything else is identical.

**Reading it.**

- **cases: 6.** Six orders over three mornings.
- **events: 24.** Six orders × four steps. That is more than the 21 calls,
  because each morning's single query counts once for each of its two orders.
- **variant x6.** A *variant* is one distinct sequence of steps. All six
  orders followed the same one, so the process is fully regular. Each step's
  name says what it was: `send_operation:createItemsShipments` is a call to
  the `send_operation` tool, on the portal operation `createItemsShipments`.
- **activities.** Each step, and how many times it happened across all cases.
- **directly-follows.** Each pair of steps where the second came straight
  after the first, and how often. This is the graph the process is drawn from,
  and what conformance checking (stage 5) compares an agent against.
- **BPMN.** The discovered process as a BPMN model: four tasks, plus a start
  and an end, joined by five sequence flows. A process that branches shows
  gateways here.

### The miner's output file: `process_model.json`

Everything the miner found is saved here. This is the file from the run above:

```json
{
  "miner": "bupaR",
  "n_cases": 6,
  "n_events": 24,
  "activities": [
    { "name": "query:orders", "frequency": 6 },
    { "name": "send_operation:createItemsShipments", "frequency": 6 },
    { "name": "call_operation:readItemsShipments", "frequency": 6 },
    { "name": "execute:orders", "frequency": 6 }
  ],
  "directly_follows": [
    { "source": "query:orders", "target": "send_operation:createItemsShipments", "frequency": 6 },
    { "source": "send_operation:createItemsShipments", "target": "call_operation:readItemsShipments", "frequency": 6 },
    { "source": "call_operation:readItemsShipments", "target": "execute:orders", "frequency": 6 }
  ],
  "variants": [
    { "sequence": ["query:orders", "send_operation:createItemsShipments",
                   "call_operation:readItemsShipments", "execute:orders"],
      "frequency": 6 }
  ],
  "bpmn": {
    "nodes": [
      { "id": "query:orders",                        "type": "task" },
      { "id": "send_operation:createItemsShipments", "type": "task" },
      { "id": "call_operation:readItemsShipments",   "type": "task" },
      { "id": "execute:orders",                      "type": "task" }
    ],
    "flows": [
      { "source": "START",                               "target": "query:orders" },
      { "source": "query:orders",                        "target": "send_operation:createItemsShipments" },
      { "source": "send_operation:createItemsShipments", "target": "call_operation:readItemsShipments" },
      { "source": "call_operation:readItemsShipments",   "target": "execute:orders" },
      { "source": "execute:orders",                      "target": "END_1" }
    ]
  }
}
```

(Each BPMN node also has a `name`, the label bupaR gives it; it is trimmed
here.) With the variant miner, `"miner"` is `"variants"` and `"bpmn"` is
`null`.

**Where the file is.** It is printed at the end of stage 2 (`-> …`):

| How you ran it | Where `process_model.json` is |
|---|---|
| `./run.sh` (containers) | `out/order_fulfillment/` next to `run.sh` |
| `uv run python examples/order_fulfillment/run.py` | `order_fulfillment_out/` in the AgenticHarvest root |
| with `--out DIR` | `DIR/` |

To load it in Python:

```python
from pathlib import Path
from agenticharvest.mining import ProcessModel

model = ProcessModel.model_validate_json(Path("out/order_fulfillment/process_model.json").read_text())
print(model.variants[0].sequence)
```

---

## 6. Stage 3: Analyze

Mining tells you **what** the process is. Analysis tells you **where it can be
better**. Three analyses run over the same log.

**Output:**

```
========================================================================
3. ANALYZE — handoffs, waits, and values carried by hand
========================================================================
  handoff  database -> webapp:directus: 6 times, 6 orders
  handoff  webapp:directus -> database: 6 times, 6 orders
  wait     query:orders -> send_operation:createItemsShipments: median 8 min
  wait     call_operation:readItemsShipments -> execute:orders: median 3 min
  wait     send_operation:createItemsShipments -> call_operation:readItemsShipments: median 3 min
  carried  customer: database (query:orders) -> webapp:directus (send_operation:createItemsShipments), 6 orders
  carried  order_id: database (query:orders) -> webapp:directus (send_operation:createItemsShipments), 6 orders
  carried  shipment_ref: webapp:directus (call_operation:readItemsShipments) -> database (execute:orders), 6 orders
```

### Handoffs: `handoffs(log, "order", systems)`

A handoff is a place where an order moves from one system to another. Every
order makes two: database to portal, then portal back to the database. Each
handoff is a point where someone switches screens and moves data.

### Waits: `performance(log, "order")`

The time between each pair of consecutive steps, per order, summarized as
median, mean, and maximum. The slowest is listed first.

`query → createItemsShipments` has a median of 8 minutes. That is longer than
the other gaps because the clerk queries once per morning and then books the
orders one after another, so the second order waits for the first. (With the
steady clock, the first order waits 3 minutes and the second 12, so the median
of the six gaps is 7.5 minutes, printed as 8.) In real use, this is where you see which
handoff people actually wait on.

### Values carried by hand: `retyped_values(log, "order", systems)`

This is the key finding. A **carry** is a value that was **read out of one
system** and later **written into another**, in the same case. Nothing
connects the two systems, so a person moved it.

To find carries, the correlator records what each call read and what it wrote,
using `ValueRule`s:

```python
VALUE_RULES = [
    # What the clerk reads out of each system...
    ValueRule(field="row",      direction="read",  tools=["query"],          result=["rows.*.*"]),
    ValueRule(field="shipment", direction="read",  tools=["call_operation"], result=["body.data.*.id"]),
    # ...and types into the other.
    ValueRule(field="order_id", direction="write", tools=["send_operation"], param="body.order_id"),
    ValueRule(field="customer", direction="write", tools=["send_operation"], param="body.customer"),
    ValueRule(field="shipment_ref", direction="write", tools=["execute"],    param="args.0"),
]
```

These show up in the log as event attributes. The query event read:

```json
{ "name": "read_values", "value": { "row": ["O-1001", "Acme Tools", 12, 9.5, "O-1002", "Bolt & Nut", 60, 3.0] } }
```

and the booking event wrote:

```json
{ "name": "written_values", "value": { "order_id": ["O-1001"], "customer": ["Acme Tools"] } }
```

`retyped_values` matches them up and finds three carries, each on all six
orders:

| Field | From | To | What the clerk does |
|---|---|---|---|
| `customer` | database (`query`) | portal (`createItemsShipments`) | copies the customer name onto the booking |
| `order_id` | database (`query`) | portal (`createItemsShipments`) | copies the order id onto the booking |
| `shipment_ref` | portal (`readItemsShipments`) | database (`execute`) | copies the shipment id back to the order |

The first two are unavoidable: the booking has to say which order it is for.
The third is different. It needs a **whole step** (`readItemsShipments`) that
does nothing but fetch a value. That is what stage 4 removes.

Matching is by value, compared as text and ignoring case, so `194` read as a
number matches `"194"` written as text. When the same value was read more than
once, the most recent read from another system gets the credit.

---

## 7. Stage 4: Redesign

**What happens.** The script compiles the mined process into an **as-is plan**,
then derives a **to-be plan** from it:

- `compile_plan` takes the dominant variant (the one most orders followed)
  and turns it into numbered steps. It looks at each step's tool: if the tool
  can change data (`Effect.WRITE`), the step is flagged as needing approval.
- `redesign` removes the look-up step and records why. The script chooses the
  steps to remove from the analysis: any `call_operation` that was the source
  of a carry.

Why is it safe to remove? The portal's booking call **already returns** the
new shipment's id in its response. The clerk looked the booking up again only
to read that id off the screen. An agent can take it straight from the
booking response.

**Output:**

```
========================================================================
4. REDESIGN — drop the steps that only carry data
========================================================================
as-is: 4 steps -> to-be: 3 steps
  removed  call_operation:readItemsShipments  (re-reads the id the booking call returned)

You fulfill the approved orders in an order database. For each approved order, compute its
net amount: qty x unit_price, less 15% when qty >= 50, 10% when qty >= 20, 5% when qty >= 10,
rounded to cents. Book one shipment for it on the shipping portal with order_id, customer
(the customer's name), net, and status 'booked f1f0731d'. Then record it in the database
with: UPDATE orders SET status = 'shipped', shipment_ref = ? WHERE id = ? — the shipment_ref
is the id of the shipment the portal created.

Follow this order fulfillment process. It was mined from 6 observed case(s); 100% of them
followed it exactly.

1. Call query on 'orders'.
2. Call send_operation on 'createItemsShipments'. This writes: pass confirm=true only once the checks above pass.
3. Call execute on 'orders'. This writes: pass confirm=true only once the checks above pass.

Use only what the tools return. If a step cannot be completed, stop and say which step failed
and why instead of improvising.

  tools granted: ['query', 'send_operation', 'execute']  (of 10 available)
```

**Reading it.**

- **The summary line** shows the change and its reason. A redesign keeps the
  as-is plan alongside the to-be plan, so the change can be reviewed.
- **The prompt** is what the agent will receive. The first paragraph is the
  goal, written for this example: the business rule, which a mined process
  can't know. The rest is generated from the to-be plan: the steps, in order,
  with every write marked.
- **The tag** (`booked f1f0731d`) is unique to this run. It marks every
  shipment the run creates, so cleanup can find them on the shared portal.
- **tools granted: 3 of 10.** The agent gets only the tools the plan uses
  (`restrict_tools`). It cannot call `describe_table`, `list_tables`, or the
  portal's read tools at all. This is least privilege: the plan defines what
  the agent may do.

The prompt is also saved to `agent_prompt.txt`.

---

## 8. Stage 5: Agentify

Run with `--agent`. Without it, the run ends after stage 4 with:

```
Re-run with --agent to hand the to-be plan to a live LLM (stage 5).
```

**What happens.**

1. Sales approves two new orders the clerk never saw: `O-2001` (30 units at
   5.00) and `O-2002` (8 units at 12.50).
2. A fresh, tapped registry is created, so the agent's own calls are recorded
   too.
3. The agent is created with the stage-4 prompt and only the three granted
   tools, and asked: *"Fulfill the approved orders in the order database at
   '…/erp.db'. The shipping portal is the web app 'directus'."*
4. The agent works through the tools on its own. Its writes still go through
   the dry-run gate: each one needs `confirm=true`.
5. The script then checks the result **in the live systems**, not in the
   agent's reply.

**Output:**

```
========================================================================
5. AGENTIFY — a live agent runs the to-be plan on new orders
========================================================================
All done! Here's a summary of everything fulfilled:

| Order | Customer | Qty | Unit Price | Gross | Discount | **Net** | Shipment ID |
|-------|--------------|-----|------------|--------|----------|---------|-------------|
| O-2001 | Acme Tools | 30 | $5.00 | $150.00 | 10% (qty ≥ 20) | **$135.00** | 200 |
| O-2002 | Crane Supply | 8 | $12.50 | $100.00 | none | **$100.00** | 201 |

Both orders are now marked **shipped** in the database with their respective shipment references (200 and 201).

  shipments on the portal and orders on disk vs. the pricing rule:
    ok  O-2001: booked net 135.0 (rule 135.0), status shipped, shipment_ref 200
    ok  O-2002: booked net 100.0 (rule 100.0), status shipped, shipment_ref 201

  order:O-2001: query:orders -> send_operation:createItemsShipments -> execute:orders
  conforms to the to-be plan: True · fitness: 1.00

  order:O-2002: query:orders -> send_operation:createItemsShipments -> execute:orders
  conforms to the to-be plan: True · fitness: 1.00
```

**Reading it.**

- **The table** is the agent's own summary. It is the LLM's text, so its exact
  wording varies between runs and models. The script does not trust it.
- **The `ok` lines** are the real check. For each new order, the script reads:
  - the shipment from the **live portal**, and checks its `net` against the
    pricing rule computed in Python;
  - the order from the **database file**, and checks its status is `shipped`;
  - that the order's `shipment_ref` **is the id of that shipment** on the
    portal.

  A line says `MISMATCH` if any of these fails.
- **The trace lines** are the agent's own calls, from its own tapped log,
  mined per order. `check_trace` replays each against the **to-be plan** (via
  `plan_model`, which turns a plan into a checkable model):
  - `conforms: True` means every step and every transition was in the plan.
  - `fitness: 1.00` means 100% of its transitions matched.

  Both orders skip the look-up step, exactly as redesigned.

The script exits with code `0` when every check passes and `1` otherwise, so
you can use it in CI.

**If no LLM is configured,** stage 5 prints this and exits with code 1:

```
  no LLM endpoint configured (Missing LLM configuration: API_BASE, API_KEY, MODEL. …); skipping stage 5.
```

---

## 9. Cleanup

At the end of every run, success or failure:

```
  cleaned up: removed 8 shipment(s) tagged f1f0731d from the portal
```

The run deletes every shipment carrying its tag from the portal: six from
stage 1, plus two from the agent. The portal is shared, so this keeps runs from
piling up. The database is a file in the output folder, so it is simply kept.

Pass `--keep` to skip the cleanup and inspect the shipments
([section 11](#11-look-at-it-yourself)).

## 10. The output files

Where they are:

- **Containers (`./run.sh`):** `out/order_fulfillment/` next to `run.sh`. The
  runner writes them into a folder mounted from your machine, so they are
  ordinary files you can open. Set `AGENTICHARVEST_OUT` to choose another folder.
- **Python (`uv run …`):** `order_fulfillment_out/` in the AgenticHarvest root.
- **Either, with `--out DIR`:** `DIR/`. (In containers, `DIR` is a path inside
  the runner; use `AGENTICHARVEST_OUT` instead.)

| File | Written by | What it holds | Open it with |
|---|---|---|---|
| `erp.db` | stage 1 | The order database after the run: all orders `shipped`, with their `shipment_ref` and `shipped_at` | `sqlite3 erp.db "select * from orders"` |
| `observed.ocel.json` | stage 1 | The event log, in the OCEL 2.0 standard format | Any OCEL tool, or a text editor |
| `process_model.json` | stage 2 | **The miner's output**: activities, directly-follows, variants, and (with bupaR) a BPMN. See [stage 2](#the-miners-output-file-process_modeljson). | A text editor, or `ProcessModel.model_validate_json` |
| `agent_prompt.txt` | stage 4 | The exact system prompt the agent received | A text editor |

The database after a run with `--agent`:

```
$ sqlite3 order_fulfillment_out/erp.db "select id, qty, unit_price, status, shipment_ref from orders"
O-1001|12|9.5|shipped|194
O-1002|60|3.0|shipped|195
O-1003|25|4.0|shipped|196
O-1004|5|20.0|shipped|197
O-1005|18|7.25|shipped|198
O-1006|50|2.4|shipped|199
O-2001|30|5.0|shipped|200
O-2002|8|12.5|shipped|201
```

The OCEL log has four top-level keys: `objectTypes`, `eventTypes`, `events`,
and `objects`. That is the standard OCEL 2.0 JSON layout, so other
process-mining tools can read it. This run had 21 events and 12 objects: the
database, its table, the portal, its two operations, its one resource, and the
six orders.

## 11. Look at it yourself

Run with `--keep`, then open the portal in a browser:

- **URL:** http://127.0.0.1:8055/admin
- **Login:** `admin@example.com` / `agenticharvest-admin`
- **Go to:** Content → `shipments`

You will see one row per order, each with its `order_id`, `customer`, `net`,
and `status booked <tag>`. Open **Activity** (the bell icon) to see Directus's
own audit trail of every create.

You can also use the tools yourself, from the AgenticHarvest root:

```bash
uv run agenticharvest call list_resources app=directus resource=shipments
uv run agenticharvest call call_operation app=directus name=readItemsShipments \
    'query={"filter": {"order_id": {"_eq": "O-1001"}}}'
```

To remove kept shipments later, run the example again (it only cleans up its
own tag), or delete them in the Directus UI.

## 12. What the safety checks do

The agent works against a live system, so the tools refuse anything unsafe
**before** a request is sent. These are real error messages:

**A write without approval** is blocked at the gateway:

```
Tool 'send_operation' has a write effect and requires 'confirm: true' to run.
```

**A write sent through the read tool** is refused, so a read-only grant really
is read-only:

```
'createItemsShipments' changes state (POST); use send_operation with confirm=true.
```

**Other checks**, on every call:

- A request whose URL leaves the portal's address is refused.
- Each path parameter is quoted into a single URL segment, so a value like
  `../../server/info` cannot reach another endpoint.
- The token is added when the request is sent. It never appears in a result,
  an event, or the log.
- Responses are capped at 20,000 characters, so a large listing cannot flood
  the agent.

## 13. Change it

Make these edits in an AgenticHarvest checkout (`examples/order_fulfillment/`)
and run from there. A standalone clone's `run.sh` runs the copy inside
`.agenticharvest`.

**Different orders.** Edit `BATCHES` and `NEW_BATCH` in `erp.py`. Each batch is
`(order id, customer id, qty, unit price)`. Keep ids in the `O-1234` form, or
change the `ORDER_ID` pattern in `workflow.py` to match.

**A different rule.** Edit `TIERS` and `net_amount` in `erp.py`, and update the
rule text in `GOAL` in `workflow.py`, so the agent is told the same rule the
check uses.

**A different process.** Edit `fulfill_by_hand` in `workflow.py`. If the clerk
stops looking the booking up, the carry disappears from stage 3 and the
redesign has nothing to remove: try it, and watch stage 4 report
`as-is: 3 steps -> to-be: 3 steps`.

**Your own systems.** The pattern carries over:

1. Put every system's tools in one registry, with a `CompositeMapper`.
2. Write one `KeyRule` per place your business key appears. Use
   `suggest_keys` if you don't know the key.
3. Add `ValueRule`s for the fields people copy between systems, then run
   `retyped_values`.
4. Mine with your business object as the case, then `compile_plan`,
   `redesign`, and check the agent against `plan_model(to_be)`.

To point AgenticHarvest at your own web app, see "Pointing AgenticHarvest at your own
systems" in the [agenticharvest-systems README](https://github.com/aster-test-generation/agenticharvest-systems#pointing-agenticharvest-at-your-own-systems).

## 14. Troubleshooting

| You see | What it means | Fix |
|---|---|---|
| `The shipping portal is not configured: Web app 'directus' is not configured …` | No `.env` with the portal's settings | Run `setup.py` (or use `run.sh`). Run host scripts from the AgenticHarvest root. |
| `directus FAILED … HTTP 401` | A token from an older stack | Run `setup.py` again; it reissues the token. |
| `… Connection refused` | Directus isn't running | `podman compose -f examples/systems/compose.yaml up -d` |
| `no LLM endpoint configured` | `--agent` without `API_BASE` / `API_KEY` / `MODEL` | Set them in the environment or `.env`. |
| `MISMATCH O-2001: …` | The agent's result in the systems differs from the rule | Read the agent's text above it to see what it did. Some models need a clearer goal: edit `GOAL`. |
| `conforms to the to-be plan: False` | The agent took a step outside the plan, e.g. re-reading the booking | The work may still be correct (see the `ok` lines). The trace shows the extra step. |

For problems starting the stack itself, see the troubleshooting table in the
[agenticharvest-systems README](https://github.com/aster-test-generation/agenticharvest-systems#troubleshooting).

## 15. How the code is organized

| File | What it holds |
|---|---|
| `erp.py` | The order database: schema and trigger, the batches, the pricing rule, and helpers to build and read it |
| `workflow.py` | The correlation rules (`KEY_RULES`, `VALUE_RULES`), the tapped registry, the clerk's process (`fulfill_by_hand`), mining, and portal cleanup |
| `run.py` | The five stages, in order, printing what you see above |
| `run.sh`, `run.ps1` | The containers-only launchers |

Everything specific to this example lives in these files. The pieces they use
from AgenticHarvest are generic:

| From AgenticHarvest | Used for |
|---|---|
| `agenticharvest.domains.sqlite`, `agenticharvest.domains.web` | The database and portal tools, and their event mappers |
| `agenticharvest.core.events`: `CompositeMapper`, `Correlator`, `KeyRule`, `ValueRule`, `OcelTap` | One log across both systems, keyed by order |
| `agenticharvest.mining`: `VariantMiner`, `collapse_repeats`, `check_trace` | Mining and conformance |
| `agenticharvest.mining.analysis`: `handoffs`, `performance`, `retyped_values` | Stage 3 |
| `agenticharvest.orchestration.compile`: `compile_plan`, `restrict_tools`; `agenticharvest.orchestration.redesign`: `redesign`, `plan_model` | Stages 4 and 5 |

AgenticHarvest's test `tests/examples/test_cross_system.py` runs stages 1 to 4
against the live portal and asserts the results shown on this page:
[test_cross_system.py](https://github.com/IBM/agenticharvest/blob/main/tests/examples/test_cross_system.py).
