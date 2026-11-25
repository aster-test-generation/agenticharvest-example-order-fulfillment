# Order fulfillment: one process across a database and a web app

A clerk fulfills approved orders every morning. The orders live in a SQLite
order database. Shipments are booked on a shipping portal, a live web app
(Directus). The two systems share no id, so neither one's logs show the
process on its own.

```
  order database (SQLite)                 shipping portal (Directus, live)
  ───────────────────────                 ────────────────────────────────
  1. query:orders            ──────────▶  2. send_operation:createItemsShipments
                                          3. call_operation:readItemsShipments
  4. execute:orders          ◀──────────     (look the booking up to copy its id)
```

This example shows how AgenticHarvest joins the two systems into one event log. It
then measures where the work crosses between systems and redesigns the process
around what it finds. Last, it hands the redesigned process to an agent and
checks the result in both systems.

> **[WALKTHROUGH.md](WALKTHROUGH.md)** goes through every stage in detail: what
> runs, the full output with an explanation of each line, the event log and
> the miner's output files, where they land, how to look at the results in the
> portal, and how to change the example.

## Run it

**With nothing installed but Podman or Docker** (and git). Python, AgenticHarvest,
and the portal run in containers:

```bash
git clone https://github.com/aster-test-generation/agenticharvest-example-order-fulfillment.git
cd agenticharvest-example-order-fulfillment
./run.sh                 # stages 1-4      (Windows: .\run.ps1)
./run.sh --agent         # + stage 5; export API_BASE, API_KEY, MODEL first
./run.sh --keep          # leave the shipments on the portal, to look at them
```

On first use, `run.sh` fetches AgenticHarvest (with this example and the live stack
as its submodules) into `./.agenticharvest`. Inside an AgenticHarvest checkout, it uses that
checkout instead. Cloned on its own, `run.sh` runs the copy of this example
inside `.agenticharvest`, so edits to the files in this clone are not used. To
change the example, work in an AgenticHarvest checkout
(`git clone --recurse-submodules https://github.com/IBM/agenticharvest.git`)
and edit `examples/order_fulfillment/`. The first run pulls about 2 GB of images and takes a few
minutes. [`agenticharvest-systems`](https://github.com/aster-test-generation/agenticharvest-systems#readme) explains what starts, how to
open the apps in a browser, and how to fix common problems.

**With Python and uv**, from the AgenticHarvest repo root:

```bash
podman compose -f examples/systems/compose.yaml up -d
uv run python examples/systems/setup.py

uv run python examples/order_fulfillment/run.py            # stages 1-4
uv run python examples/order_fulfillment/run.py --agent    # + stage 5, live LLM
```

Each run books its shipments with a unique tag and removes them from the
portal when it finishes. Pass `--keep` to leave them there for inspection.
Output files go to `order_fulfillment_out/` (change it with `--out`).

## The five stages

### 1. Observe: one log over two systems

The clerk's work runs through one tool registry that holds both the SQLite
tools and the web tools. Every call goes to a real system and is recorded as
an OCEL event.

The key piece is the **correlator**. Each domain's mapper relates a call to its
own artifact (a `database`, a `webapp`). The correlator also finds the
**business key** in the call and relates the event to it. Here the key is the
order id, found in SQL result rows, SQL arguments, request bodies and the
portal's query filter:

```python
KeyRule(object_type="order", tools=["query"],          result=["rows"], pattern=r"\bO-\d{4}\b")
KeyRule(object_type="order", tools=["send_operation"], param="body.order_id")
KeyRule(object_type="order", tools=["call_operation"], param="query.filter.order_id._eq")
```

Every event now relates to `order:O-1001`, whichever system it ran in.

### 2. Mine: one case per order

With `order` as the case, each order's trace runs across both systems. The
miner prints the variants, how often each step happened, and which step came
straight after which, plus a BPMN when R and bupaR are installed:

```
miner: bupaR · cases: 6 · events: 24
variant x6: query:orders -> send_operation:createItemsShipments
            -> call_operation:readItemsShipments -> execute:orders
  activities: 6 × each of the four steps
  directly-follows: 6 × each consecutive pair
  BPMN: 4 nodes (4 tasks), 5 sequence flows
-> order_fulfillment_out/process_model.json
```

The whole model is saved to `process_model.json` in the output folder
(`out/order_fulfillment/` next to `run.sh` when run in containers).
[WALKTHROUGH.md](WALKTHROUGH.md#5-stage-2-mine) shows the file.

### 3. Analyze: handoffs, waits, carried values

`agenticharvest.mining.analysis` reads the same log:

```
handoff  database -> webapp:directus: 6 times, 6 orders
handoff  webapp:directus -> database: 6 times, 6 orders
wait     query:orders -> send_operation:createItemsShipments: median 8 min
carried  customer:     database (query:orders) -> webapp:directus (send_operation:createItemsShipments), 6 orders
carried  order_id:     database (query:orders) -> webapp:directus (send_operation:createItemsShipments), 6 orders
carried  shipment_ref: webapp:directus (call_operation:readItemsShipments) -> database (execute:orders), 6 orders
```

A *carry* is a value that was read out of one system and later written into
another within the same case. In other words, it was copied by hand. The
correlator's `ValueRule`s record what each call read and wrote, and
`retyped_values` matches the two.

### 4. Redesign: drop the step that only carries data

The look-up step exists only to copy the shipment id back to the database, and
the booking call already returned that id. `redesign` removes the step and
records why:

```
as-is: 4 steps -> to-be: 3 steps
  removed  call_operation:readItemsShipments  (re-reads the id the booking call returned)
```

The to-be plan keeps both writes gated, and the agent is granted only the
three tools it needs.

### 5. Agentify: a live agent runs the to-be plan

The agent gets the to-be plan as its prompt and only the tools that plan uses.
It fulfills two new orders. The script then reads the results back from both
systems and checks them:

```
  ok  O-2001: booked net 135.0 (rule 135.0), status shipped, shipment_ref 32
  ok  O-2002: booked net 100.0 (rule 100.0), status shipped, shipment_ref 33

  order:O-2001: query:orders -> send_operation:createItemsShipments -> execute:orders
  conforms to the to-be plan: True · fitness: 1.00
```

`plan_model(change.to_be)` turns the to-be plan into a process model, so
`check_trace` can measure the agent against a process that was designed, not
observed.

## Files

| File | What it holds |
|---|---|
| `erp.py` | The order database: schema, a trigger, the batches, the pricing rule |
| `workflow.py` | The correlation rules and the clerk's process; shared by `run.py` and the tests |
| `run.py` | The five stages, printed |

AgenticHarvest's live test [`tests/examples/test_cross_system.py`](https://github.com/IBM/agenticharvest/blob/main/tests/examples/test_cross_system.py)
runs stages 1–4 against the portal and asserts what this page shows.
[`test_cross_system_agents.py`](https://github.com/IBM/agenticharvest/blob/main/tests/examples/test_cross_system_agents.py)
runs stage 5 (`pytest -m real_llm`).

## Make it yours

The pattern carries over to any set of systems:

1. Put every system's tools in one registry, with a `CompositeMapper` from
   domain to mapper.
2. Write one `KeyRule` per place the business key appears. If you don't know
   the key yet, record a session with an `InMemoryTap` and call `suggest_keys`:
   it proposes the value shapes that appear in more than one system.
3. Add `ValueRule`s for the fields people copy between systems, then call
   `retyped_values` to see where they do it.
4. Mine with `case_type` set to your business object, compile, `redesign`, and
   check the agent against `plan_model(to_be)`.
