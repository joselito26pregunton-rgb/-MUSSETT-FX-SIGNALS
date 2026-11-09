from datetime import timedelta
from sqlalchemy import select
from models import BotState, Event, Notification, Signal, aware, utcnow
from transition_policy import target_for, validate_chain

DISCLAIMER = "Este mensaje es informativo y no constituye una orden de inversión."


def lock_state(db):
    # The singleton serializes ingestion, pause/resume and expiration transactions.
    return db.scalar(select(BotState).where(BotState.id == 1).with_for_update())


def queue(db, key, kind, text, signal_id=None, paused=False):
    db.add(Notification(key=key, kind=kind, text=text, signal_id=signal_id,
                        state="skipped" if paused else "queued"))


def initial_text(signal):
    p = signal.payload
    label = {"BUY": "POSIBLE COMPRA ↑", "SELL": "POSIBLE VENTA ↓", "WAIT": "ESPERAR · SIN CONFIGURACIÓN CONFIRMADA"}
    marker = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}
    trend = p.get("trend_status") or p.get("trend")
    trend_label = {"BULLISH": "ALCISTA", "BEARISH": "BAJISTA", "NEUTRAL": "LATERAL"}
    confirmation = p.get("signal_status") or p.get("confirmation")
    confirmation_label = {"CONFIRMED": "CONFIRMADA", "PROVISIONAL": "PROVISIONAL",
                          "UNCONFIRMED": "NO CONFIRMADA"}
    volume_label = {"TICK": "volumen de ticks, no centralizado", "BASE": "unidades del activo base",
                    "QUOTE": "unidades de cotización", "UNKNOWN": "tipo no identificado por la fuente",
                    "UNAVAILABLE": "no disponible"}
    position = "POR ENCIMA" if signal.price > p["sma200"] else "POR DEBAJO" if signal.price < p["sma200"] else "AL MISMO NIVEL"
    short_spread = p.get("gmma_short_spread")
    long_spread = p.get("gmma_long_spread")
    test_banner = "PRUEBA FICTICIA · NO ES UNA SEÑAL REAL\n\n" if p.get("is_test") else ""
    return (
        f"{test_banner}{marker[signal.direction]} MUSSETT FX SIGNALS\n\nALERTA RECIBIDA\n\nEUR/USD\n\n{label[signal.direction]}\n\n"
        f"Precio: {signal.price:.5f}\nTemporalidad: {signal.timeframe}\n\n"
        f"ANÁLISIS TÉCNICO:\n"
        f"* SMA 200: {p['sma200']}; precio {position}\n"
        f"* GMMA: {p['gmma_status']}\n"
        f"  Spread corto: {f'{short_spread:.6f}%' if short_spread is not None else 'NO PROPORCIONADO'}\n"
        f"  Spread largo: {f'{long_spread:.6f}%' if long_spread is not None else 'NO PROPORCIONADO'}\n"
        f"* EMA 14: {p['ema14']}\n* RSI: {p['rsi']}\n"
        f"* Volumen: {p['volume_status']}; {volume_label.get(p.get('volume_type'), 'TIPO NO PROPORCIONADO')}\n"
        f"  Relativo: {p.get('relative_volume') if p.get('relative_volume') is not None else 'NO PROPORCIONADO'}\n"
        f"* Tendencia: {trend_label.get(trend, 'NO PROPORCIONADA')}\n"
        f"* Confirmación: {confirmation_label.get(confirmation, 'NO PROPORCIONADA')}\n"
        f"Estructura: {p.get('structure') or 'NO PROPORCIONADA'}\n"
        f"\n"
        f"Hora de recepción UTC: {signal.received_at.isoformat()}\n"
        f"VENTANA DE REVISIÓN: 03:40\nCaducidad UTC: {signal.expires_at.isoformat()}\n"
        f"ID: {signal.signal_id}\nESTADO: PENDIENTE DE VALIDACIÓN HUMANA.\n"
        f"Los 220 segundos no son una recomendación de entrada.\n{DISCLAIMER}"
    )


def invalidate(db, signal, paused, reason):
    signal.status = "INVALIDATED"
    for item in db.scalars(select(Notification).where(
        Notification.signal_id == signal.signal_id, Notification.kind == "initial",
        Notification.state == "queued"
    )):
        item.state = "skipped"
    db.add(Event(signal_id=signal.signal_id, kind="invalidated"))
    queue(db, f"invalidated:{signal.signal_id}", "invalidation",
          f"{'PRUEBA FICTICIA · ' if signal.payload.get('is_test') else ''}MUSSETT FX SIGNALS\nEUR/USD\nEscenario {signal.signal_id} CANCELADO / INVALIDADO.\n"
          f"{reason}\nLa ventana anterior no se renueva.\n{DISCLAIMER}", signal.signal_id, paused)


def ingest(db, alert, now=None):
    now = now or utcnow()
    state = lock_state(db)
    if db.get(Signal, alert.signal_id):
        return None
    target = target_for(db, alert) if alert.event_type in ("INVALIDATION", "TRANSITION") else None
    validate_chain(db, alert, now, target)
    if alert.event_type in ("INVALIDATION", "TRANSITION"):
        if target.status == "PENDING" and aware(target.expires_at) > now:
            invalidate(db, target, state.paused, "Invalidación explícita recibida en el webhook.")
        else:
            # A delayed standalone invalidation is an auditable no-op. It must
            # never search for a replacement target or modify the new state.
            db.add(Event(signal_id=target.signal_id, kind="invalidation_noop"))
    elif (alert.schema_version != 3 and alert.direction in ("BUY", "SELL")
          and alert.signal_status != "PROVISIONAL"):
        pending = db.scalars(select(Signal).where(
            Signal.status == "PENDING", Signal.timeframe == alert.timeframe,
            Signal.direction == ("SELL" if alert.direction == "BUY" else "BUY"),
            Signal.expires_at > now
        )).all()
        for old in pending:
            if bool(old.payload.get("is_test")) != alert.is_test:
                continue  # A synthetic test must never cancel a real scenario.
            if old.payload.get("scenario_id") != alert.scenario_id:
                continue  # An old unscoped alert cannot cancel a version 3 chain.
            invalidate(db, old, state.paused, "Se recibió una señal opuesta en la misma temporalidad.")
    payload = alert.model_dump(mode="json", exclude={"secret"})
    signal = Signal(signal_id=alert.signal_id, symbol="EURUSD", direction=alert.direction,
                    timeframe=alert.timeframe, price=alert.price, payload=payload,
                    status="INVALIDATION" if alert.event_type == "INVALIDATION" else "PENDING",
                    received_at=now, expires_at=now + timedelta(seconds=220))
    db.add(signal)
    db.flush()
    db.add(Event(signal_id=signal.signal_id, kind="received"))
    if alert.event_type in ("SIGNAL", "TRANSITION"):
        queue(db, f"initial:{signal.signal_id}", "initial", initial_text(signal),
              signal.signal_id, state.paused)
    return signal
