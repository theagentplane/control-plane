"""Repositories: the only code that runs queries. One per concept, all tenant-scoped."""

from dataclasses import dataclass

from sqlalchemy import Engine

from control_plane.repositories.api_key_repository import ApiKeyRepository
from control_plane.repositories.envelope_repository import EnvelopeRepository
from control_plane.repositories.governance_repository import GovernanceRepository
from control_plane.repositories.ledger_repository import LedgerRepository
from control_plane.repositories.trace_repository import TraceRepository


@dataclass(frozen=True)
class Repositories:
    """Every repository, built over one engine."""

    traces: TraceRepository
    envelopes: EnvelopeRepository
    governance: GovernanceRepository
    ledger: LedgerRepository
    api_keys: ApiKeyRepository


def build_repositories(engine: Engine) -> Repositories:
    """Construct the repositories over ``engine``."""
    return Repositories(
        traces=TraceRepository(engine),
        envelopes=EnvelopeRepository(engine),
        governance=GovernanceRepository(engine),
        ledger=LedgerRepository(engine),
        api_keys=ApiKeyRepository(engine),
    )


__all__ = [
    "ApiKeyRepository",
    "EnvelopeRepository",
    "GovernanceRepository",
    "LedgerRepository",
    "Repositories",
    "TraceRepository",
    "build_repositories",
]
