# MUSSETT FX SIGNALS

Asistente informativo EUR/USD: TradingView → FastAPI → PostgreSQL → Telegram.

## Run & Operate

- Managed workflow `artifacts/api-server: API Server` runs Python Uvicorn, not the unused Express scaffold.
- `python initialize.py` — explicit development schema initialization, never automatic startup DDL
- `python -m pytest -q` — fictional tests, including temporary PostgreSQL schemas; DEVELOPMENT DB ONLY
- `python scripts/check_telegram.py` — safely check the real bot credential without printing it
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL`; configure `WEBHOOK_SECRET`, `API_ACCESS_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` for live integrations.

## Stack

- Product backend: Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, psycopg, HTTPX.
- Python modules live at repository root to match the requested standalone Render layout.
- The pre-existing pnpm workspace and Express scaffold are retained but do not serve this product.

## Where things live

- `README.md`: full Spanish setup for Telegram, TradingView, GitHub and Render.
- `main.py`, `models.py`, `schemas.py`: API, persistent models, validation.
- `signal_manager.py`, `expiration_manager.py`: transactions, outbox, timers and bot commands.
- `pine_script/mussett_fx_signals.pine`: independent Pine v6 indicator.
- `render.yaml`: paid always-on web service and external PostgreSQL blueprint.

## Architecture decisions

- Retain the user-requested Python/FastAPI/SQLAlchemy stack rather than substitute the template's Express/Drizzle stack.
- Telegram cannot guarantee exactly-once delivery: ambiguous outcomes are not auto-retried, prioritizing duplicate prevention over guaranteed delivery.
- Target deployment is Render; do not publish, purchase resources or push to GitHub without user authorization.
- Protocol 3 uses one TRANSITION with scenario_id and an exact predecessor because separate HTTP alerts cannot guarantee order. Reject expired or consumed predecessors rather than silently rebuilding or renewing a chain.
- Pine cannot restore prior IDs or read webhook responses. After a restart, use a new run-scoped scenario, observe closed bars silently for 225 seconds, and never replay the initial direction; older accepted signals retain their 220-second backend expiry.

## Product

- Validated EUR/USD BUY/SELL/WAIT alerts, 220-second review window, opposite/explicit invalidation.
- Persistent Telegram notifications and commands; pause does not stop ingestion or expiration.
- Separate manually recorded hypothetical-entry timer of 180 seconds, never broker execution.

## User preferences

- Never execute trades, connect to EO Broker to operate, manage money, or request bank/broker credentials.
- TradingView computes indicators; do not invent missing data, probabilities or guarantees.
- Do not claim an integration works until actually tested. Token verification is not delivery verification.
- Prepare GitHub/Render instructions without artificial keep-alive pings.
- Phase 2 must keep the existing product intact and target a real Render HTTPS endpoint, not a Replit development URL.
- Do not call TradingView connected until a genuine alert is correlated with server records; synthetic checks are not proof.

## Gotchas

- Credentials are read from env and never logged; HTTP client debug logging can expose Telegram URLs.
- Missing webhook/API secrets fail closed (503), not with default credentials.
- Missing Telegram chat disables polling and deliveries; use the discovery helper after sending /start.
- API routing is owned by the API artifact. Docs at `/api/docs`; exact requested endpoints also routed.
- PostgreSQL advisory leadership and a singleton state row serialize timers and mutations.
- Phase 2 spreads are percentages of close. Preserve legacy contracts; new payloads must provide the complete phase 2 field group.
- Provisional opposite setups must not cancel confirmed scenarios. Synthetic tests must never invalidate real signals.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
