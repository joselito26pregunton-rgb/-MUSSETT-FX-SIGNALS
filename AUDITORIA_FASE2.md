# Auditoría y entrega — Fase 2

**Actualización posterior:** el error de orden entre dos HTTP independientes se corrige con protocolo 3 y un único evento TRANSITION. Los detalles vigentes, límites y políticas de error están en `TRANSICIONES_ATOMICAS.md`. Las referencias a dos emisiones independientes más abajo describen el indicador anterior.

## Hallazgos antes de modificar código

- Se inventariaron los archivos versionados del proyecto y se revisó el backend Python activo, el indicador, el contrato, las pruebas, la configuración de ejecución y el Blueprint. Los componentes React/Express del scaffold no sirven este producto y se conservaron.
- Endpoint real: **POST /webhook/tradingview**; no es `/api/webhook/tradingview`. La ruta existe en el router incluido por FastAPI y en OpenAPI.
- Telegram: cliente HTTPX independiente, long polling para comandos, chat autorizado configurado y cola persistente. `getMe` devolvió OK. Los envíos de comandos de la fase anterior constan como `sent` con confirmación de Telegram.
- PostgreSQL: `/health` devolvió `{"server":"ok","database":"ok"}`.
- Configurados: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. No se copiaron sus valores a este informe.
- Pendientes: **WEBHOOK_SECRET** y **API_ACCESS_KEY**.
- Los registros repetían `GET / HTTP/1.1 404 Not Found` desde solicitudes locales. La causa verificable era la ausencia de un manejador para `/`; no correspondían a peticiones al webhook. Se añadió una respuesta informativa 200 sin exponer datos privados.
- El contrato anterior carecía de `gmma_short_spread`, `gmma_long_spread`, `trend_status` y `signal_status`.
- El Pine anterior comparaba separación de grupos, pero no validaba toda su orientación y expansión.
- La consulta de despliegue de Replit devolvió `isDeployed=false`. No se dispone de una URL de Render configurada/verificada. Esto no demuestra que el usuario no tenga servicios de Render fuera de este proyecto.

## Rutas verificables

El contrato activo se consulta en `GET /api/openapi.json`:

- `GET /` — identificación y rutas útiles.
- `GET /health` — servidor y conexión PostgreSQL.
- `GET /api/healthz` — alias conservado.
- `GET /api`, `GET /api/` — redirección a documentación.
- `GET /api/docs`, `GET /api/openapi.json` — documentación.
- `GET /status`, `GET /signals/latest`, `GET /signals/history` — protegidas con X-API-Key.
- `POST /signals/{signal_id}/entry`, `GET /signals/{signal_id}/entry` — reloj hipotético independiente.
- `POST /telegram/test` — envío real manual protegido.
- `GET /events` — auditoría protegida.
- `POST /webhook/tradingview` — recepción protegida por secreto JSON.

## Cambios y compatibilidad

No se reconstruyó el proyecto ni se eliminó la funcionalidad anterior.

- Contrato fase 2: usar `schema_version: 2`. Además de los campos originales, requiere ambos spreads GMMA, `trend_status` y `signal_status`. El formato de indicadores anterior sigue admitido. Tras la corrección de seguridad autorizada, **ambas versiones requieren `is_test` explícito como booleano JSON**, sin valor predeterminado. Si se proporciona cualquiera de los cuatro campos nuevos, se exige el grupo completo.
- Spreads: porcentajes del precio de cierre, `100 × (máxima EMA del grupo − mínima EMA del grupo) / close`.
- `trend_status`: BULLISH, BEARISH o NEUTRAL.
- `signal_status`: PROVISIONAL, CONFIRMED o INVALIDATED.
- `volume_type`: TICK, BASE, QUOTE, UNKNOWN o UNAVAILABLE. No se asume volumen Forex centralizado.
- `direction: INVALIDATION` se normaliza a WAIT + `event_type: INVALIDATION` + `signal_status: INVALIDATED`, manteniendo compatibilidad con la columna de dirección original. Requiere `invalidates_signal_id`.
- Las invalidaciones y señales opuestas solo cancelan escenarios del mismo marco temporal. Una provisional opuesta no cancela un escenario confirmado.
- Se conservan los 220 segundos desde la recepción, sin renovación; la entrada hipotética conserva sus 180 segundos independientes.
- Los eventos `is_test: true` se identifican como ficticios, también en `/last`, y no pueden cancelar escenarios reales. Omitir `is_test`, enviar `null`, cadenas o números produce `422` sin persistir ni encolar señales. El Pine actualizado declara `is_test: false`; las alertas anteriores deben recrearse.
- El webhook registra ID, tipo, indicador de prueba y tiempo de procesamiento después de confirmar la transacción. No registra cuerpo, token ni secreto. Un `is_test: false` por sí solo **no demuestra** procedencia de TradingView.

## Indicador y revalidación

El archivo completo es `pine_script/mussett_fx_signals.pine`.

- Solo EUR/USD, velas estándar y marco de **1 minuto**.
- SMA 200 como filtro; EMA 14 con impulso; RSI como confirmación; GMMA completo con orden, pendiente de los centros, separación mínima y expansión de ambos grupos.
- Pivotes solo confirmados, sin desplazarlos hacia el pasado ni usar `lookahead`.
- Evaluación intrabar continua y panel PROVISIONAL. **No se envían provisionales como alertas confirmadas**: la publicación ocurre al cierre.
- BUY_SETUP, SELL_SETUP, WAIT e INVALIDATION separados.
- Se invalida al perder la configuración en un cierre confirmado; el backend cancela al recibir el evento, sin esperar al vencimiento. No se promete invalidación definitiva intrabar.
- Las invalidaciones solo apuntan a IDs realmente publicados en tiempo real, no a supuestas señales históricas.
- No se emite otra señal automáticamente porque hayan pasado 220 segundos.
- Falta compilarlo en el editor real de TradingView: las comprobaciones estáticas no equivalen a una compilación Pine.

## Webhook público de producción: pendiente

La ruta exacta es **/webhook/tradingview**, método **POST**.

No existe una URL pública verificada que se pueda entregar como operativa. Tras desplegar en Render:

```text
https://<DOMINIO-REAL-DEL-SERVICIO-RENDER>/webhook/tradingview
```

La cadena anterior es un **formato**, no una URL existente. Copia el dominio real desde Render. No uses el dominio de desarrollo de Replit.

El Blueprint conserva `0.0.0.0`, `$PORT`, `/health`, base PostgreSQL persistente y variables privadas configurables en Render. El servidor es independiente de Replit; las credenciales y la base de Render se configuran allí.

## Verificación

Los resultados de cada caso se generan con:

```bash
python -m pytest -v --junitxml=generated-artifacts/phase2-tests.xml -rA
python scripts/build_test_report.py generated-artifacts/phase2-tests.xml generated-artifacts/phase2-tests.html
```

La prueba de latencia crea cinco recepciones ficticias con PostgreSQL y comprueba cada una contra el objetivo de 1 segundo. No es una garantía del tiempo de red, de Render ni de TradingView.

Para una comprobación deliberada sobre Render ya configurado:

```bash
python scripts/verify_webhook.py --base-url https://<DOMINIO-REAL-RENDER>
```

El script exige secretos del servicio destino en el entorno, envía una prueba WAIT ficticia etiquetada, verifica 202, 409 y persistencia. Puede generar notificaciones de prueba en Telegram. **No demuestra conexión de TradingView**.

## Cuándo considerar TradingView conectado

1. Desplegar en Render y comprobar HTTPS y `/health`.
2. Configurar allí las cinco variables de conexión.
3. Compilar el indicador, configurar su secreto y crear una alerta “Any alert() function call”.
4. Colocar el dominio real más `/webhook/tradingview` en la URL webhook de esa alerta.
5. Recibir una alerta auténtica del indicador.
6. Correlacionar el ID y la hora entre el registro de alertas de TradingView, `webhook accepted` en el servidor, el historial persistido y Telegram.

Hasta completar esos pasos, el estado correcto es **preparado para integración, no conectado a TradingView**.

## Resultados obtenidos en esta entrega

- **63 pruebas automáticas aprobadas**, ninguna fallida ni omitida.
- Cinco recepciones ficticias con PostgreSQL: **27,1–108,6 ms** en este entorno. El objetivo inferior a un segundo se cumplió en esa muestra, no se ha medido Render.
- `/health`: servidor y base de datos OK.
- Tras reiniciar, los registros muestran **GET / → 200**, en lugar de los anteriores 404.
- OpenAPI contiene el webhook POST y los campos nuevos.
- Se envió **un mensaje real de prueba del nuevo diseño a Telegram** y Telegram devolvió confirmación de envío. El mensaje estaba claramente marcado PRUEBA FICTICIA; no se creó un escenario real ni se atribuyó a TradingView.
- La batería mostró una advertencia de deprecación del cliente de pruebas Starlette/HTTPX; no provocó fallos.
- Resultados individuales: `generated-artifacts/phase2-tests.html`; evidencia para herramientas: `generated-artifacts/phase2-tests.xml`.
- Pendientes fuera del entorno local: compilación real del indicador, dominio público Render, configuración privada del webhook y recepción auténtica desde TradingView.
