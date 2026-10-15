"""Concurrent writers on the default SQLite file (issue 28).

A Chronicle recorder and TokenOps ledger writers hit one database at once. Writers queue behind
the write lock instead of failing with ``database is locked``, so nothing answers 5xx.
"""

import threading
from collections.abc import Callable
from typing import Any

from agentplane_primitives.enums import State
from agentplane_primitives.models import Trace
from agentplane_primitives.utils import new_trace_id
from fastapi.testclient import TestClient

Make = Callable[..., TestClient]
WRITES = 40


def _ledger_event() -> dict[str, Any]:
    return {
        "kind": "spent_add",
        "trace_id": "a" * 32,
        "delta_micros": 1,
        "targets": [{"budget_id": "b", "segment_key": "trace:x", "period": "lifetime"}],
    }


def test_chronicle_and_ledger_writers_do_not_lock_each_other_out(make_client: Make) -> None:
    client = make_client()
    statuses: list[int] = []

    def ledger_writer() -> None:
        for _ in range(WRITES):
            response = client.post("/v1/ledger/events:batch", json={"events": [_ledger_event()]})
            statuses.append(response.status_code)

    def recorder() -> None:
        for _ in range(WRITES):
            trace = Trace(trace_id=new_trace_id(), name="run", state=State.OPEN)
            response = client.post(
                "/v1/traces", json=trace.model_dump(mode="json", exclude_none=True)
            )
            statuses.append(response.status_code)

    threads = [threading.Thread(target=ledger_writer) for _ in range(2)]
    threads.append(threading.Thread(target=recorder))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(statuses) == 3 * WRITES
    assert [s for s in statuses if s >= 500] == []
