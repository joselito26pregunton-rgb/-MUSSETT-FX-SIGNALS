import secrets
import logging
from time import perf_counter
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from schemas import Alert, SignalOut
from signal_manager import ingest
from models import Signal, utcnow
from transition_policy import TransitionConflict

router = APIRouter()
log = logging.getLogger("uvicorn.error")


@router.post("/webhook/tradingview", status_code=202, response_model=SignalOut)
def receive(alert: Alert, request: Request):
    started = perf_counter()
    received_at = utcnow()
    configured = request.app.state.settings.webhook_secret
    if not configured:
        raise HTTPException(503, "WEBHOOK_SECRET no configurado")
    if not secrets.compare_digest(configured.get_secret_value().encode(), alert.secret.get_secret_value().encode()):
        raise HTTPException(401, "Secreto incorrecto")
    try:
        with request.app.state.factory.begin() as db:
            # Check duplicate identity before freshness: a retried accepted
            # event is always a conflict and must never mutate its old target.
            if db.get(Signal, alert.signal_id):
                raise HTTPException(409, "signal_id duplicado")
            if alert.schema_version == 3 or alert.scenario_id:
                age = (received_at - alert.candle_time).total_seconds()
                settings = request.app.state.settings
                if age > settings.webhook_max_age_seconds or age < -settings.webhook_future_tolerance_seconds:
                    raise HTTPException(422, "candle_time fuera de la ventana temporal permitida")
            signal = ingest(db, alert, received_at)
            if signal is None:
                raise HTTPException(409, "signal_id duplicado")
            result = SignalOut.model_validate(signal)
        log.info("webhook accepted signal_id=%s event=%s is_test=%s elapsed_ms=%.2f",
                 alert.signal_id, alert.event_type, alert.is_test, (perf_counter() - started) * 1000)
        return result
    except IntegrityError:
        raise HTTPException(409, "signal_id duplicado") from None
    except TransitionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
