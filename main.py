import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from config import Settings
from database import make_database
from models import BotState, Event, HypotheticalEntry, Notification, Signal, aware, utcnow
from schemas import EntryIn, RecoveryAuditDetails, RecoveryIn, SignalOut
from signal_manager import lock_state
from expiration_manager import worker
from tradingview_webhook import router
from telegram_bot import TelegramBot, TelegramFailure
from webhook_security import WebhookGuards
from transition_policy import latest_chain_state

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def protect(request: Request, x_api_key: str | None = Header(default=None)):
    key = request.app.state.settings.api_access_key
    if not key:
        raise HTTPException(503, "API_ACCESS_KEY no configurada")
    if not x_api_key or not secrets.compare_digest(key.get_secret_value().encode(), x_api_key.encode()):
        raise HTTPException(401, "Acceso no autorizado")


def authenticate_recovery_operator(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_recovery_operator_id: str | None = Header(default=None),
    x_recovery_operator_token: str | None = Header(default=None),
):
    # Keep the existing API-key gate active while independently authenticating
    # the individual who authorized the recovery.
    protect(request, x_api_key)
    credentials = request.app.state.settings.recovery_operator_credentials
    if not credentials:
        raise HTTPException(503, "Operadores de recuperación no configurados")
    expected = credentials.get(x_recovery_operator_id) if x_recovery_operator_id else None
    if (not expected or not x_recovery_operator_token
            or not secrets.compare_digest(
                expected.get_secret_value().encode(), x_recovery_operator_token.encode()
            )):
        raise HTTPException(401, "Operador de recuperación no autorizado")
    return x_recovery_operator_id


def create_app(settings=None, engine=None, factory=None):
    settings = settings or Settings()
    if engine is None:
        engine, factory = make_database(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app):
        # DDL is explicit (python database.py), never on application startup.
        with factory.begin() as db:
            db.execute(text("SELECT 1"))
            # Seed via the explicit initialization command, not startup.
            if not db.get(BotState, 1):
                raise RuntimeError("Ejecute python initialize.py antes de iniciar el servicio")
        app.state.worker_healthy = False
        app.state.telegram_status = "not_checked"
        app.state.bot = TelegramBot(
            settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None,
            settings.telegram_chat_id
        )
        task = asyncio.create_task(worker(app)) if settings.worker_enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await app.state.bot.close()
            engine.dispose()

    app = FastAPI(
        title="MUSSETT FX SIGNALS", version="2.0.0",
        description="Análisis informativo EUR/USD. Sin ejecución de operaciones. "
                    "Los endpoints privados requieren la cabecera X-API-Key.",
        lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json",
        redoc_url=None
    )
    app.state.settings, app.state.engine, app.state.factory = settings, engine, factory
    app.add_middleware(WebhookGuards, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default validation errors can echo the submitted webhook secret.
        errors = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible"})

    @app.get("/api", include_in_schema=False)
    @app.get("/api/", include_in_schema=False)
    def docs_redirect():
        return RedirectResponse("/api/docs")

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "MUSSETT FX SIGNALS", "health": "/health",
                "docs": "/api/docs", "webhook": "/webhook/tradingview", "webhook_method": "POST"}

    @app.get("/health")
    @app.get("/api/healthz", include_in_schema=False)
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"server": "ok", "database": "ok"}
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"server": "ok", "database": "unavailable"})

    @app.get("/status", dependencies=[Depends(protect)])
    def status():
        with factory() as db:
            state = db.get(BotState, 1)
            counts = dict(db.execute(select(Signal.status, func.count()).group_by(Signal.status)).all())
            delivery = dict(db.execute(select(Notification.state, func.count()).group_by(Notification.state)).all())
            last = db.scalar(select(Signal).order_by(Signal.received_at.desc()).limit(1))
            return {
                "paused": state.paused, "signals": counts, "notifications": delivery,
                "telegram_configured": settings.telegram_configured,
                "telegram_status": app.state.telegram_status,
                "worker_healthy": app.state.worker_healthy,
                "webhook_configured": bool(settings.webhook_secret),
                "review_seconds": 220, "automatic_trading": False,
                "last_webhook": ({"signal_id": last.signal_id, "received_at": aware(last.received_at),
                                  "is_test": bool(last.payload.get("is_test"))} if last else None)
            }

    @app.get("/signals/latest", response_model=SignalOut | None, dependencies=[Depends(protect)])
    def latest():
        with factory() as db:
            return db.scalar(select(Signal).order_by(Signal.received_at.desc()).limit(1))

    @app.get("/signals/history", response_model=list[SignalOut], dependencies=[Depends(protect)])
    def history(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                direction: str | None = Query(default=None, pattern="^(BUY|SELL|WAIT)$")):
        with factory() as db:
            query = select(Signal)
            if direction:
                query = query.where(Signal.direction == direction)
            return db.scalars(query.order_by(Signal.received_at.desc()).limit(limit).offset(offset)).all()

    @app.get("/recovery/scenarios/{scenario_id}", dependencies=[Depends(protect)])
    def recovery_state(scenario_id: str, is_test: bool = Query(...)):
        """Inspect one chain without mixing synthetic tests with live signals."""
        with factory() as db:
            latest = latest_chain_state(db, scenario_id, is_test)
            if not latest:
                return {"scenario_id": scenario_id, "is_test": is_test, "exists": False,
                        "latest": None, "active": False}
            expiry = aware(latest.expires_at)
            pending = latest.status == "PENDING" and expiry > utcnow()
            return {
                "scenario_id": scenario_id, "is_test": is_test, "exists": True,
                "active": pending and latest.direction in ("BUY", "SELL"),
                "latest": {
                    "signal_id": latest.signal_id, "direction": latest.direction,
                    "status": latest.status, "received_at": aware(latest.received_at),
                    "expires_at": expiry, "candle_time": latest.payload.get("candle_time"),
                    "pending": pending,
                },
            }

    @app.post("/recovery/scenarios/{scenario_id}")
    def authorize_recovery(
        scenario_id: str, data: RecoveryIn,
        operator_id: str = Depends(authenticate_recovery_operator),
    ):
        """Authorize and durably audit a manual TradingView pointer correction."""
        now = utcnow()
        rejection = None
        with factory.begin() as db:
            lock_state(db)
            latest = latest_chain_state(db, scenario_id, data.is_test)
            if data.action == "SYNC_LOCAL_POINTER":
                if not latest:
                    rejection = (404, "No existe un estado aceptado para sincronizar")
                elif latest.signal_id != data.signal_id:
                    rejection = (409, "Sincronice únicamente el último signal_id aceptado")
                elif latest.status != "PENDING" or aware(latest.expires_at) <= now:
                    rejection = (409, "El último estado ya no está pendiente y vigente; limpie el puntero")
            elif (latest and latest.direction in ("BUY", "SELL")
                  and latest.status == "PENDING" and aware(latest.expires_at) > now):
                rejection = (409, "Hay un escenario BUY/SELL vigente; sincronice su puntero")

            audit_id = f"recovery:{secrets.token_urlsafe(18)}"
            details = RecoveryAuditDetails(
                outcome="rejected" if rejection else "authorized",
                operator_id=operator_id,
                scenario_id=scenario_id,
                is_test=data.is_test,
                action=data.action,
                signal_id=data.signal_id,
                rejected_signal_id=data.rejected_signal_id,
                reason=data.reason.strip(),
                requested_at=now,
                resolved_signal_id=latest.signal_id if latest else None,
                authorized_at=None if rejection else now,
                rejection_reason=rejection[1] if rejection else None,
            ).model_dump(mode="json", exclude_none=True)
            if not rejection:
                db.add(Event(signal_id=latest.signal_id if latest else None, kind="manual_recovery"))
            # Keep an immutable, queryable audit record without adding a schema
            # migration. A skipped notification is never visible to Telegram.
            db.add(Notification(
                key=audit_id, signal_id=latest.signal_id if latest else None,
                kind="recovery_audit", text=json.dumps(details, ensure_ascii=False),
                state="skipped",
            ))

        if rejection:
            raise HTTPException(rejection[0], rejection[1])

        return {
            "authorized": True, "audit_id": audit_id, **details,
            "direction": latest.direction if data.action == "SYNC_LOCAL_POINTER" else None,
            "expires_at": aware(latest.expires_at) if data.action == "SYNC_LOCAL_POINTER" else None,
            "manual_only": True, "signals_modified": False,
        }

    @app.get("/recovery/audit", dependencies=[Depends(protect)])
    def recovery_audit(limit: int = Query(default=50, ge=1, le=200)):
        with factory() as db:
            records = db.scalars(select(Notification).where(
                Notification.kind == "recovery_audit"
            ).order_by(Notification.created_at.desc()).limit(limit)).all()
            return [{
                "audit_id": record.key,
                "created_at": aware(record.created_at),
                "details": json.loads(record.text),
            } for record in records]

    @app.post("/signals/{signal_id}/entry", dependencies=[Depends(protect)], status_code=201)
    def hypothetical_entry(signal_id: str, data: EntryIn):
        with factory.begin() as db:
            lock_state(db)
            signal = db.get(Signal, signal_id)
            if not signal:
                raise HTTPException(404, "Señal no encontrada")
            if signal.direction == "WAIT" or signal.status == "INVALIDATION":
                raise HTTPException(422, "No se registra entrada hipotética para WAIT o invalidación")
            if db.get(HypotheticalEntry, signal_id):
                raise HTTPException(409, "Entrada ya registrada")
            if data.entered_at > utcnow():
                raise HTTPException(422, "La entrada debe ser una hora ya registrada, no futura")
            expiry = data.entered_at + timedelta(seconds=180)
            db.add(HypotheticalEntry(signal_id=signal_id, entered_at=data.entered_at, expires_at=expiry))
            return {"signal_id": signal_id, "entered_at": data.entered_at, "expires_at": expiry,
                    "hypothetical_only": True, "review_expires_at": aware(signal.expires_at)}

    @app.get("/signals/{signal_id}/entry", dependencies=[Depends(protect)])
    def get_entry(signal_id: str):
        with factory() as db:
            item = db.get(HypotheticalEntry, signal_id)
            if not item:
                raise HTTPException(404, "Entrada no encontrada")
            return {"signal_id": item.signal_id, "entered_at": aware(item.entered_at),
                    "expires_at": aware(item.expires_at), "hypothetical_only": True}

    @app.post("/telegram/test", dependencies=[Depends(protect)])
    async def telegram_test():
        # A deliberate manual test, never performed automatically on startup.
        if not settings.telegram_configured:
            raise HTTPException(503, "Configure TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
        try:
            result = await app.state.bot.send(
                "MUSSETT FX SIGNALS\nPrueba de conexión. Solo información, sin operaciones automáticas."
            )
            app.state.telegram_status = "connected"
            return {"sent": True, "message_id": result.get("message_id")}
        except TelegramFailure as exc:
            app.state.telegram_status = exc.code
            raise HTTPException(502, exc.code) from None

    @app.get("/events", dependencies=[Depends(protect)])
    def events(limit: int = Query(default=50, ge=1, le=200)):
        with factory() as db:
            return [{"id": e.id, "signal_id": e.signal_id, "kind": e.kind, "created_at": aware(e.created_at)}
                    for e in db.scalars(select(Event).order_by(Event.id.desc()).limit(limit))]

    app.include_router(router)
    return app


app = create_app()
