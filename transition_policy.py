"""Version 3 chain rules; all functions run under the caller's state-row lock."""
from datetime import datetime
from sqlalchemy import select
from models import Event, Signal, aware


class TransitionConflict(ValueError):
    """A validly shaped event conflicts with an already committed scenario."""


def target_for(db, alert):
    target = db.get(Signal, alert.invalidates_signal_id)
    if not target or target.timeframe != alert.timeframe:
        raise ValueError("Escenario objetivo inexistente o temporalidad distinta")
    if bool(target.payload.get("is_test")) != alert.is_test:
        raise ValueError("Una prueba no puede invalidar un escenario real, ni viceversa")
    target_scenario = target.payload.get("scenario_id")
    if target_scenario and target_scenario != alert.scenario_id:
        raise ValueError("El objetivo pertenece a otro escenario")
    if alert.event_type == "TRANSITION":
        if not target_scenario or target_scenario != alert.scenario_id:
            raise ValueError("TRANSITION requiere un objetivo con el mismo scenario_id")
        if target.direction not in ("BUY", "SELL") or target.status == "INVALIDATION":
            raise ValueError("El objetivo de TRANSITION debe ser un escenario BUY o SELL")
    return target


def latest_chain_state(db, scenario_id, is_test):
    """Return the last accepted event for one explicitly classified chain."""
    return db.scalar(select(Signal).join(Event, Event.signal_id == Signal.signal_id).where(
        Event.kind == "received", Signal.status != "INVALIDATION",
        Signal.payload["scenario_id"].as_string() == scenario_id,
        Signal.payload["is_test"].as_boolean() == is_test
    ).order_by(Event.id.desc()).limit(1))


def validate_chain(db, alert, now, target=None):
    if alert.schema_version != 3 and not alert.scenario_id:
        return
    # The append-only receipt event determines commit order, even if two
    # reception timestamps happen to be equal.
    latest = latest_chain_state(db, alert.scenario_id, alert.is_test)
    if alert.event_type == "INVALIDATION":
        return  # Legacy/standalone cancellation never advances the state chain.
    if latest:
        latest_time = datetime.fromisoformat(latest.payload["candle_time"].replace("Z", "+00:00"))
        if alert.candle_time <= latest_time:
            raise TransitionConflict("candle_time no es posterior al último estado del escenario")
    if alert.event_type == "TRANSITION":
        if target.status != "PENDING" or aware(target.expires_at) <= now:
            raise TransitionConflict("El objetivo ya fue invalidado, venció o no está pendiente")
        if not latest or latest.signal_id != target.signal_id:
            raise TransitionConflict("El objetivo ya no es el último estado del escenario")
    elif (latest and latest.direction in ("BUY", "SELL") and latest.status == "PENDING"
          and aware(latest.expires_at) > now):
        raise TransitionConflict("Existe un escenario vigente: envíe TRANSITION con su ID")
