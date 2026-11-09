"""Persistent outbox and expiration worker. One worker leader per PostgreSQL DB."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, text, func
from models import BotState, Event, Notification, Signal, aware, utcnow
from signal_manager import DISCLAIMER, lock_state, queue
from telegram_bot import TelegramFailure

log = logging.getLogger("mussett")


def expire_pending(factory, now=None):
    now = now or utcnow()
    with factory.begin() as db:
        state = lock_state(db)
        pending = db.scalars(select(Signal).where(
            Signal.status == "PENDING", Signal.expires_at <= now
        )).all()
        for signal in pending:
            signal.status = "EXPIRED"
            db.add(Event(signal_id=signal.signal_id, kind="expired"))
            for item in db.scalars(select(Notification).where(
                Notification.signal_id == signal.signal_id,
                Notification.kind == "initial", Notification.state == "queued"
            )):
                item.state = "skipped"
            queue(db, f"expired:{signal.signal_id}", "expiration",
                  f"{'PRUEBA FICTICIA · ' if signal.payload.get('is_test') else ''}MUSSETT FX SIGNALS\nEUR/USD\nEscenario {signal.signal_id} CADUCADO.\n"
                  f"Terminó la ventana de revisión de 03:40 a las {aware(signal.expires_at).isoformat()}.\n"
                  f"No es el vencimiento de una operación.\n{DISCLAIMER}",
                  signal.signal_id, state.paused)
        return len(pending)


COMMANDS = {
    "/start": "MUSSETT FX SIGNALS activo. Solo análisis EUR/USD, sin ejecución de operaciones. /help",
    "/help": "/start /status /last /help /pause /resume\n"
             "BUY y SELL son escenarios informativos; WAIT significa confirmación insuficiente.\n"
             "Revisión humana de 220 segundos, no recomendación de entrada ni garantía de beneficios.",
}


def process_update(factory, update, allowed_chat):
    uid = update.get("update_id")
    if not isinstance(uid, int):
        return
    with factory.begin() as db:
        state = lock_state(db)
        if uid < state.update_offset:
            return
        state.update_offset = uid + 1
        msg = update.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != str(allowed_chat):
            return
        command = (msg.get("text") or "").split(" ", 1)[0].split("@", 1)[0]
        if command == "/pause":
            state.paused = True
            for item in db.scalars(select(Notification).where(
                Notification.state == "queued", Notification.kind != "command"
            )):
                item.state = "skipped"
            reply = "Notificaciones pausadas. El servidor sigue guardando y caducando las señales."
        elif command == "/resume":
            state.paused = False
            reply = "Notificaciones reanudadas. No se reenviarán señales antiguas."
        elif command == "/status":
            count = db.scalar(select(func.count()).select_from(Signal).where(Signal.status == "PENDING"))
            reply = f"Servidor activo. Notificaciones: {'PAUSADAS' if state.paused else 'ACTIVAS'}.\nEscenarios pendientes: {count}."
        elif command == "/last":
            last = db.scalar(select(Signal).order_by(Signal.received_at.desc()).limit(1))
            reply = (("PRUEBA FICTICIA · NO ES UNA SEÑAL REAL\n\n" if last.payload.get("is_test") else "") +
                     f"Último escenario: {last.signal_id}\n{last.direction} · {last.price:.5f}\n"
                     f"Estado: {last.status}\nCaducidad UTC: {aware(last.expires_at).isoformat()}\n{DISCLAIMER}"
                     if last else "Todavía no se han recibido señales.")
        else:
            reply = COMMANDS.get(command)
        if reply:
            queue(db, f"command:{uid}", "command", reply)


async def dispatch_one(factory, bot):
    with factory.begin() as db:
        state = lock_state(db)
        item = db.scalar(select(Notification).where(
            Notification.state == "queued", Notification.next_attempt_at <= utcnow()
        ).order_by(Notification.created_at, Notification.key).limit(1).with_for_update())
        if not item:
            return False
        if state.paused and item.kind != "command":
            item.state = "skipped"
            return True
        if item.kind == "initial":
            signal = db.get(Signal, item.signal_id)
            if signal.status != "PENDING" or aware(signal.expires_at) <= utcnow():
                item.state = "skipped"
                return True
        key, message = item.key, item.text
        item.state = "dispatching"
        item.attempts += 1
    try:
        sent = await bot.send(message)
    except TelegramFailure as exc:
        with factory.begin() as db:
            item = db.get(Notification, key)
            item.state = ("uncertain" if exc.ambiguous else
                          "queued" if exc.retryable and item.attempts < 5 else "failed")
            item.next_attempt_at = utcnow() + timedelta(seconds=exc.retry_after)
        log.warning("Telegram delivery result: %s", exc.code)
    else:
        with factory.begin() as db:
            item = db.get(Notification, key)
            item.state, item.message_id = "sent", sent.get("message_id")
    return True


async def worker(app):
    while True:
        try:
            await worker_attempt(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            app.state.worker_healthy = False
            log.error("Worker/database unavailable; retrying in 5 seconds (details suppressed for security).")
            await asyncio.sleep(5)


async def worker_attempt(app):
    """Advisory lock prevents multiple pollers/outbox dispatchers on PostgreSQL."""
    engine, factory, bot = app.state.engine, app.state.factory, app.state.bot
    leader = engine.connect()
    pg = engine.dialect.name == "postgresql"
    locked = False
    try:
        while True:
            if pg and not locked:
                locked = leader.execute(text("SELECT pg_try_advisory_lock(714220)")).scalar()
                leader.commit()
                if not locked:
                    await asyncio.sleep(5)
                    continue
            if not pg:
                locked = True
            app.state.leader_connection = leader
            # A crash during delivery has an ambiguous outcome: do not resend blindly.
            with factory.begin() as db:
                for item in db.scalars(select(Notification).where(Notification.state == "dispatching")):
                    item.state = "uncertain"
            await run_leader(app)
    except asyncio.CancelledError:
        raise
    finally:
        if pg and locked and not leader.invalidated:
            try:
                leader.execute(text("SELECT pg_advisory_unlock(714220)"))
                leader.commit()
            except Exception:
                pass
        leader.close()


async def run_leader(app):
    factory, bot, settings = app.state.factory, app.state.bot, app.state.settings
    poll_task = None
    try:
        if settings.telegram_configured and settings.telegram_polling_enabled:
            poll_task = asyncio.create_task(poll_commands(app))
        while True:
            # Fail closed if the dedicated advisory-lock connection was lost.
            # No reconnect here: a new connection would not own the old lock.
            if app.state.engine.dialect.name == "postgresql":
                app.state.leader_connection.execute(text("SELECT 1"))
                app.state.leader_connection.commit()
            await asyncio.to_thread(expire_pending, factory)
            app.state.worker_healthy = True
            if settings.telegram_configured:
                # Bounded batch, keeping expiration responsive.
                for _ in range(5):
                    if not await dispatch_one(factory, bot):
                        break
            await asyncio.sleep(settings.worker_interval)
    finally:
        if poll_task:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)


async def poll_commands(app):
    bot, factory = app.state.bot, app.state.factory
    try:
        info = await bot.call("getWebhookInfo")
        if info.get("url"):
            app.state.telegram_status = "webhook_conflict"
            return  # Never remove someone else's webhook automatically.
    except TelegramFailure as exc:
        app.state.telegram_status = exc.code
        return
    while True:
        try:
            with factory() as db:
                offset = db.get(BotState, 1).update_offset
            updates = await bot.call("getUpdates", {
                "offset": offset, "timeout": 20, "allowed_updates": ["message"]
            })
            app.state.telegram_status = "connected"
            for update in updates:
                await asyncio.to_thread(process_update, factory, update, app.state.settings.telegram_chat_id)
        except TelegramFailure as exc:
            app.state.telegram_status = exc.code
            await asyncio.sleep(exc.retry_after)
        except asyncio.CancelledError:
            raise
        except Exception:
            app.state.telegram_status = "polling_error"
            await asyncio.sleep(15)
