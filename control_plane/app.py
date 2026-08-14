"""AgentPlane control-plane HTTP API.

Hosts:
  * TokenOps governance / runs / ledger (from tokenops-cp-split)
  * Chronicle envelope ingest + query (RFC control-plane-api)
  * Bearer auth with tenant + scopes when CONTROL_PLANE_API_KEYS is set
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from control_plane.auth import Principal, env_key_views, parse_api_keys, require_scopes
from control_plane.envelope_store import EnvelopeStore
from control_plane.models import RunAlreadyRegisteredError, parse_governance_mode
from control_plane.serde import (
    budget_from_dict,
    budget_to_dict,
    policy_from_dict,
    policy_to_dict,
    registration_from_dict,
    registration_to_dict,
    run_from_dict,
    run_to_dict,
    segment_from_dict,
    segment_to_dict,
)
from control_plane.settings import Settings, load_settings
from control_plane.store import SqliteStore, new_id


def create_app(
    store: SqliteStore | None = None,
    envelopes: EnvelopeStore | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    cfg = settings or load_settings()
    gov = store or SqliteStore(cfg.db_path)
    env_store = envelopes or EnvelopeStore(cfg.db_path)
    owns = store is None

    app = FastAPI(
        title="agentplane-control-plane",
        version=cfg.version,
        description="SQLite-backed AgentPlane control plane. Callers: agent-chronicle, "
        "agent-tokenops, ui. See docs/DESIGN.md.",
    )
    app.state.settings = cfg
    app.state.api_keys = parse_api_keys(cfg.api_keys)
    app.state.store = gov
    app.state.envelopes = env_store

    from control_plane.web import mount_web

    mount_web(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "control-plane", "version": cfg.version}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        # Cheap DB ping
        gov.list_budgets()
        return {"status": "ready"}

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if owns:
            gov.close()
            env_store.close()

    def _durability(request: Request) -> str:
        header = (request.headers.get("Durability") or cfg.default_durability).strip().lower()
        if header not in {"sync", "queued"}:
            raise HTTPException(status_code=400, detail="Durability must be sync|queued")
        # queued: v1 acks after SQLite commit (same as sync) — placeholder for real queue
        return header

    # ---- Chronicle envelopes (agent ingest = batch only) ------------------ #

    @app.post("/v1/envelopes:batch", tags=["agent-chronicle"])
    async def ingest_batch(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> JSONResponse:
        """Caller: Chronicle sidecar. Only ingest API. batch_size=1 is immediate flush."""
        if int(request.headers.get("content-length") or 0) > cfg.max_body_bytes:
            raise HTTPException(status_code=413, detail="payload too large")
        payload = await request.json()
        items = payload.get("envelopes") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="expected {envelopes: [...]}")
        if len(items) > cfg.max_batch:
            raise HTTPException(status_code=400, detail=f"batch max {cfg.max_batch}")
        durability = _durability(request)
        try:
            result = env_store.append_many(principal.tenant_id, items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(
            {
                "status": "stored",
                "accepted": result["accepted"],
                "deduped": result["deduped"],
                "envelope_ids": result["envelope_ids"],
                "durability": durability,
            },
            status_code=201,
        )

    @app.get("/v1/traces/{trace_id}", tags=["agent-chronicle", "ui"])
    async def get_trace(
        trace_id: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, Any]:
        """Caller: Chronicle sidecar (replay) or Chronicle UI."""
        tr = env_store.get_trace(principal.tenant_id, trace_id)
        if tr is None:
            raise HTTPException(status_code=404, detail="not found")
        return tr

    @app.get("/v1/traces/{trace_id}/envelopes", tags=["agent-chronicle", "ui"])
    async def list_trace_envelopes(
        trace_id: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, Any]:
        """Caller: Chronicle sidecar (full fixture for mocks) or Chronicle UI waterfall."""
        return {"envelopes": env_store.list_trace_envelopes(principal.tenant_id, trace_id)}

    @app.get("/v1/traces", tags=["ui"])
    async def find_traces(
        q: str | None = None,
        session_id: str | None = None,
        message_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, Any]:
        """Caller: Chronicle UI — search / filter traces. Not used by the agent sidecar."""
        traces = env_store.search_traces(
            principal.tenant_id,
            q=q,
            session_id=session_id,
            message_id=message_id,
            user_id=user_id,
            limit=min(limit, 500),
            offset=max(offset, 0),
        )
        return {"traces": traces}

    # ---- TokenOps registration -------------------------------------------- #

    @app.post("/v1/runs")
    async def register_run(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> JSONResponse:
        payload = await request.json()
        run_id = str(payload.get("run_id") or "").strip() or new_id("run")
        try:
            mode = parse_governance_mode(payload.get("mode") or payload.get("governance_mode"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reg = registration_from_dict(
            {
                "run_id": run_id,
                "intent": payload.get("intent", ""),
                "user_dims": payload.get("user_dims") or {},
                "mode": mode.value,
            }
        )
        try:
            saved = gov.register_run(reg)
        except RunAlreadyRegisteredError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        # Chronicle session coupling intentionally omitted — agents own their traces.
        return JSONResponse(
            {
                "run_id": saved.run_id,
                "status": "registered",
                "mode": saved.mode.value,
                "intent": saved.intent,
                "user_dims": dict(saved.user_dims),
            },
            status_code=201,
        )

    @app.get("/v1/runs/{run_id}/registration")
    async def get_registration(
        run_id: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> JSONResponse:
        reg = gov.get_run_registration(run_id)
        if reg is None:
            return JSONResponse({"error": f"run {run_id!r} is not registered"}, status_code=404)
        return JSONResponse(registration_to_dict(reg))

    # ---- governance ------------------------------------------------------- #

    @app.get("/v1/governance/{agent}")
    async def governance_for_agent(
        agent: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, Any]:
        return gov.governance_config_for(agent)

    @app.get("/v1/segments")
    async def list_segments(principal: Principal = Depends(require_scopes("read"))) -> list[dict[str, Any]]:
        return [segment_to_dict(s) for s in gov.list_segments()]

    @app.put("/v1/segments")
    async def upsert_segment(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, Any]:
        seg = segment_from_dict(await request.json())
        return segment_to_dict(gov.upsert_segment(seg))

    @app.get("/v1/segments/{sid}")
    async def get_segment(
        sid: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> JSONResponse:
        seg = gov.get_segment(sid)
        if seg is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(segment_to_dict(seg))

    @app.delete("/v1/segments/{sid}")
    async def delete_segment(
        sid: str,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, str]:
        gov.delete_segment(sid)
        return {"status": "deleted"}

    @app.get("/v1/budgets")
    async def list_budgets(principal: Principal = Depends(require_scopes("read"))) -> list[dict[str, Any]]:
        return [budget_to_dict(b) for b in gov.list_budgets()]

    @app.put("/v1/budgets")
    async def upsert_budget(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, Any]:
        spec = budget_from_dict(await request.json())
        return budget_to_dict(gov.upsert_budget(spec))

    @app.get("/v1/budgets/{bid}")
    async def get_budget(
        bid: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> JSONResponse:
        spec = gov.get_budget(bid)
        if spec is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(budget_to_dict(spec))

    @app.delete("/v1/budgets/{bid}")
    async def delete_budget(
        bid: str,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, str]:
        gov.delete_budget(bid)
        return {"status": "deleted"}

    @app.get("/v1/policies")
    async def list_policies(principal: Principal = Depends(require_scopes("read"))) -> list[dict[str, Any]]:
        return [policy_to_dict(p) for p in gov.list_policy_instances()]

    @app.put("/v1/policies")
    async def upsert_policy(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, Any]:
        pi = policy_from_dict(await request.json())
        try:
            saved = gov.upsert_policy_instance(pi)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return policy_to_dict(saved)

    @app.get("/v1/policies/{pid}")
    async def get_policy(
        pid: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> JSONResponse:
        pi = gov.get_policy_instance(pid)
        if pi is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(policy_to_dict(pi))

    @app.delete("/v1/policies/{pid}")
    async def delete_policy(
        pid: str,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, str]:
        gov.delete_policy_instance(pid)
        return {"status": "deleted"}

    # ---- run history ------------------------------------------------------ #

    @app.put("/v1/run-records")
    async def create_run_record(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, Any]:
        rec = run_from_dict(await request.json())
        return run_to_dict(gov.create_run(rec))

    @app.patch("/v1/run-records/{run_id}")
    async def update_run_record(
        run_id: str,
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, str]:
        fields = await request.json()
        gov.update_run(run_id, **fields)
        return {"status": "updated"}

    @app.get("/v1/run-records/{run_id}")
    async def get_run_record(
        run_id: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> JSONResponse:
        rec = gov.get_run(run_id)
        if rec is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(run_to_dict(rec))

    @app.get("/v1/run-records")
    async def list_run_records(
        problematic_only: bool = False,
        limit: int = 200,
        principal: Principal = Depends(require_scopes("read")),
    ) -> list[dict[str, Any]]:
        return [run_to_dict(r) for r in gov.list_runs(problematic_only=problematic_only, limit=limit)]

    @app.get("/v1/run-records/tag-keys")
    async def run_tag_keys(
        limit: int = 500,
        principal: Principal = Depends(require_scopes("read")),
    ) -> list[str]:
        return gov.run_tag_keys(limit=limit)

    # ---- ledger ----------------------------------------------------------- #

    @app.post("/v1/ledger/spent/add")
    async def ledger_add_spent(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, int]:
        body = await request.json()
        spent = gov.ledger_add_spent(
            body["budget_id"], body["segment_key"], body.get("period", "lifetime"), int(body["delta"]),
        )
        return {"spent_micros": spent}

    @app.get("/v1/ledger/spent")
    async def ledger_get_spent(
        budget_id: str,
        segment_key: str,
        period: str = "lifetime",
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, int]:
        return {"spent_micros": gov.ledger_get_spent(budget_id, segment_key, period)}

    @app.post("/v1/ledger/inflight/admit")
    async def ledger_admit(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, int]:
        body = await request.json()
        return {"count": gov.ledger_admit(body["segment_key"])}

    @app.post("/v1/ledger/inflight/complete")
    async def ledger_complete(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, int]:
        body = await request.json()
        return {"count": gov.ledger_complete(body["segment_key"])}

    @app.get("/v1/ledger/inflight")
    async def ledger_inflight(
        segment_key: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, int]:
        return {"count": gov.ledger_inflight(segment_key)}

    @app.post("/v1/ledger/halt/mark")
    async def ledger_mark_halted(
        request: Request,
        principal: Principal = Depends(require_scopes("ingest")),
    ) -> dict[str, str]:
        body = await request.json()
        gov.ledger_mark_halted(body["run_id"], body.get("reason", ""))
        return {"status": "halted"}

    @app.get("/v1/ledger/halt/{run_id}")
    async def ledger_halt_status(
        run_id: str,
        principal: Principal = Depends(require_scopes("read")),
    ) -> dict[str, Any]:
        return {
            "halted": gov.ledger_is_halted(run_id),
            "halt_reason": gov.ledger_halt_reason(run_id),
        }

    @app.post("/v1/ledger/halt/clear")
    async def ledger_clear_halt(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, str]:
        body = await request.json()
        gov.ledger_clear_halt(body["run_id"])
        return {"status": "cleared"}

    # ---- admin ------------------------------------------------------------ #

    @app.post("/v1/admin/seed-if-empty")
    async def seed_if_empty(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, bool]:
        body = await request.json()
        seeded = gov.seed_default_governance_if_empty(body.get("governance"))
        return {"seeded": seeded}

    @app.post("/v1/admin/clear-all")
    async def clear_all(principal: Principal = Depends(require_scopes("admin"))) -> dict[str, str]:
        gov.clear_all()
        return {"status": "cleared"}

    @app.post("/v1/admin/clear-governance")
    async def clear_governance(principal: Principal = Depends(require_scopes("admin"))) -> dict[str, str]:
        gov.clear_governance()
        return {"status": "cleared"}

    @app.post("/v1/admin/reseed-governance")
    async def reseed_governance(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, bool]:
        body = await request.json()
        seeded = gov.reseed_governance(body.get("governance"))
        return {"seeded": seeded}

    # ---- API keys (Admin UI) ---------------------------------------------- #

    @app.get("/v1/admin/keys", tags=["ui"])
    async def list_keys(principal: Principal = Depends(require_scopes("admin"))) -> dict[str, Any]:
        """Caller: Admin UI. Env keys include the secret (already on the host). DB keys show prefix only."""
        db_keys = [k.__dict__ for k in gov.list_api_keys()]
        env_keys = [k.__dict__ for k in env_key_views(cfg.api_keys)]
        return {"keys": env_keys + db_keys, "auth_disabled": not cfg.api_keys and not gov.list_api_keys()}

    @app.post("/v1/admin/keys", tags=["ui"])
    async def create_key(
        request: Request,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, Any]:
        """Caller: Admin UI. Secret is returned once."""
        body = await request.json()
        name = str(body.get("name") or "").strip()
        tenant = str(body.get("tenant_id") or "local").strip()
        scopes = body.get("scopes") or ["ingest", "read"]
        if isinstance(scopes, str):
            scopes = [s for s in scopes.replace("+", ",").split(",") if s.strip()]
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        created = gov.create_api_key(name=name, tenant_id=tenant, scopes=scopes)
        return created.__dict__

    @app.delete("/v1/admin/keys/{kid}", tags=["ui"])
    async def delete_key(
        kid: str,
        principal: Principal = Depends(require_scopes("admin")),
    ) -> dict[str, str]:
        """Caller: Admin UI. Env keys cannot be deleted here."""
        if kid.startswith("env:"):
            raise HTTPException(status_code=400, detail="env keys are removed by unsetting CONTROL_PLANE_API_KEYS")
        gov.delete_api_key(kid)
        return {"status": "deleted"}

    return app


# ASGI entry: uvicorn control_plane.app:create_app --factory
