from __future__ import annotations

import os
import time

from .graph import Neo4jProjector
from .ledger import PostgresLedger
from .model import FactEvent


def main() -> None:
    ledger = PostgresLedger(os.environ["SECRETARY_DATABASE_URL"])
    ledger.migrate()
    graph = Neo4jProjector(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    while True:
        pending = ledger.pending("neo4j")
        for item in pending:
            try:
                graph.project(FactEvent.from_dict(item["event"]))
                ledger.mark_delivered(item["seq"])
            except Exception as exc:
                ledger.mark_failed(item["seq"], str(exc))
        time.sleep(1 if pending else 5)


if __name__ == "__main__":
    main()
