# Transiciones atómicas — protocolo 3

## Alcance y seguridad de datos

Se conserva el backend, los comandos de Telegram, la cola persistente y los plazos de 220/180 segundos. No se cambian tablas ni columnas, no se eliminan registros y no se migra ninguna base de producción. Los metadatos nuevos se almacenan en el JSON existente.

No se han configurado alertas auténticas, publicado cambios ni enviado mensajes de producción a Telegram durante esta implementación.

## Nuevo contrato

El indicador utiliza `schema_version: 3` y un `scenario_id` por ejecución de la alerta, instancia del indicador, proveedor y temporalidad. `signal_id` continúa siendo único por evento. `scenario_id` identifica la cadena; `invalidates_signal_id` identifica un evento concreto anterior de esa cadena.

Primer estado: `event_type: SIGNAL`, sin objetivo de invalidación.

Cuando una configuración BUY o SELL se pierde o cambia de dirección: **una única llamada `alert()`**, con `event_type: TRANSITION`, la nueva dirección BUY/SELL/WAIT y `invalidates_signal_id`. No se envía una segunda alerta de invalidación independiente.

Ejemplo ilustrativo de una transición BUY → WAIT (números y hora ficticios; no enviarlo como señal real):

```json
{
  "secret": "CONFIGURAR_PRIVADAMENTE",
  "schema_version": 3,
  "is_test": false,
  "scenario_id": "mussett-main-OANDA:EURUSD-1-1794250680000",
  "signal_id": "mussett-main-OANDA:EURUSD-1-1794250680000-1794250860000",
  "event_type": "TRANSITION",
  "invalidates_signal_id": "mussett-main-OANDA:EURUSD-1-1794250680000-1794250800000",
  "symbol": "EURUSD",
  "direction": "WAIT",
  "timeframe": "1",
  "price": 1.165,
  "sma200": 1.164,
  "ema14": 1.1651,
  "rsi": 49.5,
  "gmma_status": "MIXED",
  "gmma_short_spread": 0.02,
  "gmma_long_spread": 0.01,
  "volume_status": "NORMAL",
  "relative_volume": 0.95,
  "volume_type": "TICK",
  "trend_status": "BULLISH",
  "structure": "MIXED",
  "signal_status": "CONFIRMED",
  "candle_time": "2026-11-09T19:01:00Z"
}
```

Pine envía `is_test: false` porque sus valores proceden del gráfico. Las pruebas deben usar siempre `true`. Esa clasificación **no prueba** que una solicitud externa sea auténtica.

## Garantía transaccional

1. `receive()` autentica el secreto y comprueba antigüedad del evento.
2. `factory.begin()` abre una sola transacción.
3. `ingest()` bloquea la fila singleton de estado mediante `SELECT ... FOR UPDATE`.
4. Comprueba de nuevo duplicados, objetivo, temporalidad, clasificación, cadena y orden de vela.
5. El objetivo debe ser el último estado de esa cadena, BUY/SELL, pendiente y no vencido.
6. Se invalida exactamente ese objetivo y se anula su aviso inicial si sigue en cola.
7. Se registra el nuevo estado con su plazo de 220 segundos desde su propia recepción.
8. Los eventos de auditoría y los avisos de cancelación/nuevo estado se guardan dentro de la misma transacción.
9. Solo al completarse todo se hace COMMIT. Cualquier fallo hace ROLLBACK de todos los cambios, incluidos los que ya hicieron `flush()`.

El worker de Telegram solo puede observar los avisos después del COMMIT. **La atomicidad es de base de datos**, no de la red Telegram: un aviso anterior ya enviado o en vuelo no puede retirarse. Un fallo posterior de Telegram no deshace el historial persistido.

## Política explícita de errores

| Situación | Respuesta / efecto |
|---|---|
| Transición válida | 202; objetivo invalidado y nuevo estado guardado juntos. |
| Mismo `signal_id`, incluso reintento con otro objetivo | 409; ningún cambio ni renovación. |
| Objetivo inexistente, otra temporalidad, otro `scenario_id` o distinta clasificación de prueba | 422; ningún cambio. |
| Objetivo WAIT o evento de invalidación usado como origen | 422; no es un origen BUY/SELL válido para TRANSITION. |
| Objetivo vencido, invalidado o que ya no es el último estado | 409; no se registra parcialmente el nuevo estado. |
| Dos transiciones concurrentes sobre el mismo objetivo | Solo una confirma; la otra recibe conflicto. |
| Nuevo SIGNAL de una cadena con BUY/SELL vigente, sin invalidación atómica | 409; debe usar TRANSITION. |
| Vela no posterior al último estado de la cadena | 409; no se aplica una transición atrasada. |
| Antigüedad superior a 300 s o más de 60 s en el futuro | 422 para versión 3 o eventos con `scenario_id`. |
| INVALIDATION independiente tardía contra un objetivo ya terminal | No-op auditable, sin buscar otro objetivo ni cancelar el nuevo estado. |

Una transición tardía contra un escenario vencido no lo reactiva y no se convierte automáticamente en otro SIGNAL. Esto implica que el indicador puede recibir rechazo si su ID anterior ya expiró: no hay renovación ni reconstrucción silenciosa de la cadena. Un nuevo SIGNAL independiente debe corresponder a un nuevo evento, con otro ID y una vela posterior; no se genera desde un temporizador.

## Recuperación manual de estado local de TradingView

TradingView no entrega al script la respuesta HTTP del webhook. Por eso Pine puede avanzar `previousScenarioId` aunque la solicitud se haya perdido, haya recibido `409/422` o el script se haya reiniciado y haya olvidado el estado aceptado. La recuperación es siempre una decisión manual autenticada; no modifica señales existentes, no reintenta una vela vieja y no extiende su vencimiento.

1. Consulta el estado de la cadena con `GET /recovery/scenarios/{scenario_id}?is_test=false` y la cabecera `X-API-Key`. Usa `is_test=true` para una cadena ficticia. Las dos clasificaciones se consultan y recuperan por separado.
2. Si el último estado es BUY/SELL y sigue pendiente/vigente, autoriza `SYNC_LOCAL_POINTER` en `POST /recovery/scenarios/{scenario_id}` con su `signal_id` exacto, la misma clasificación y un motivo. Solo el último estado aceptado puede sincronizarse. Introduce en TradingView los valores devueltos de `signal_id`, `scenario_id` y dirección. Un WAIT vigente también puede sincronizarse, aunque no es un objetivo de TRANSITION.
3. Si no existe un estado aceptado, o el último estado es WAIT, vencido o terminal, autoriza `CLEAR_LOCAL_POINTER` con motivo y, opcionalmente, el ID local rechazado. Deja vacíos los tres campos manuales de Pine. El borrado se rechaza con `409` si hay un BUY/SELL pendiente vigente: primero hay que sincronizar ese estado.
4. Tras limpiar, Pine no envía durante 225 segundos desde el reinicio. Después de ese período, una nueva variación en una vela confirmada puede emitir un `SIGNAL` con ID y cadena nuevos. No se vuelve a enviar el webhook rechazado. Su ventana de 220 segundos comienza solo si el backend acepta ese evento; el margen de arranque no cambia ni renueva esa ventana.
5. Tras cada reinicio del indicador, repite la consulta y la autorización: Pine pierde su estado `var`. Los campos manuales no se pueden considerar actuales después de una ejecución posterior.

`GET /recovery/audit` devuelve las autorizaciones registradas, sus motivos, clasificación, acción, IDs y hora. El registro se guarda como notificación `skipped` y evento `manual_recovery`; no se encola ni se envía a Telegram. Estas rutas requieren `X-API-Key`. El registro identifica la clave autorizada, no a una persona individual.

Interpreta `409` como conflicto de estado/ID: consulta de nuevo, sincroniza el último evento aceptado o limpia solo si ya no hay un BUY/SELL vigente. Un `422` requiere corregir el cuerpo, la temporalidad, el `scenario_id`, `is_test` o el objetivo; no se debe cambiar a un contrato antiguo para eludir la validación. Si la vela ya quedó fuera de la ventana temporal, espera una vela confirmada nueva después de corregir el estado. Ninguna ruta de recuperación cambia el estado de una señal ni su `expires_at`.

## Compatibilidad

Las versiones 1 y 2, incluidos sus eventos INVALIDATION independientes, siguen admitidas con `is_test` explícito. Sus parejas de solicitudes antiguas **no adquieren atomicidad retroactivamente**; la garantía nueva corresponde a TRANSITION.

Una alerta antigua sin `scenario_id` no puede cancelar por oposición una cadena nueva identificada. La cancelación explícita de un objetivo nuevo exige su `scenario_id` correcto. Un mensaje con cadena no puede eludir sus reglas enviando `schema_version: 2`.

Tras actualizar el indicador, **recrear la alerta de TradingView**: una alerta anterior conserva la copia antigua del código y seguirá enviando dos eventos independientes.

## Reinicio y recreación de alertas Pine

Pine Script no puede consultar el webhook para recuperar el último `signal_id`, y sus variables `var` se reinicializan cuando se añade el script o se recrea la alerta. No afirmamos que se pueda sincronizar automáticamente con el backend.

Para recuperarse sin arriesgar una cancelación al ID equivocado:

1. Cada ejecución de Pine usa un `scenario_id` nuevo derivado de su hora de inicio; los IDs de evento incluyen esa ejecución. Un evento anterior no se atribuye falsamente a la nueva.
2. La primera vela real cerrada solo establece la dirección base. No envía BUY, SELL ni WAIT.
3. Durante **225 segundos** desde el reinicio, Pine actualiza esa referencia con velas cerradas, pero no emite alertas. El plazo supera los 220 segundos originales de caducidad en cinco segundos; no cambia ni renueva el TTL del backend.
4. Al terminar la espera, solo un cambio posterior de dirección emite un evento nuevo. Si la dirección inicial sigue siendo BUY/SELL, Pine no la repite automáticamente; si sigue siendo WAIT, tampoco envía WAIT ficticio.
5. La señal aceptada antes del reinicio conserva su caducidad original de 220 segundos y el worker existente la marca vencida. No se invalida con un ID supuesto. Si el worker se reinicia, procesa la caducidad almacenada al volver.

Consecuencia conservadora: al reiniciar se puede perder una alerta para una condición BUY/SELL que ya estaba presente y no vuelve a cambiar. Es preferible a duplicarla, reactivarla o invalidar un evento incorrecto. Si el servidor no recibió la señal previa, la misma espera aplica de todas formas. El nuevo `scenario_id` de ejecución permite comenzar una cadena nueva después de observar un cambio real, en vez de quedar atascado por una cadena antigua.

Pine no recibe la respuesta del POST y no puede saber si el backend rechazó un evento. Para volver a sincronizar después de sospechar una entrega fallida, recrea la alerta y aplica de nuevo este inicio seguro; no reutilices ni adivines un ID anterior.

## Autenticación y protección contra abuso

`WEBHOOK_SECRET` es una credencial compartida que autoriza al portador a llamar al webhook. Se compara con `secrets.compare_digest`, no se imprime, se omite del JSON almacenado y no se coloca en el código.

**No es una firma emitida por TradingView ni una prueba de procedencia.** Cualquiera que conozca el secreto puede construir una petición válida. HTTPS protege el transporte; la primera conexión auténtica debe verificarse correlacionando el registro de TradingView con el servidor y Telegram.

Medidas implementadas:

- Secreto obligatorio: 503 si no está configurado, 401 si no coincide.
- `is_test` obligatorio y estrictamente booleano.
- Clave única `signal_id` y comprobación dentro del bloqueo de transacción.
- Cadena y orden de vela para rechazar eventos atrasados o reintentos con nuevos IDs sobre un origen consumido.
- Cuerpo máximo de **16 KiB**, incluido cuerpo recibido por fragmentos: 413.
- Máximo global de **120 solicitudes/minuto por proceso**, incluidas las no autorizadas: 429 con `Retry-After`.
- Espera máxima de **5 segundos para el cuerpo completo**, también si llega por fragmentos: 408.
- Ventana de tiempo de versión 3: 300 segundos de antigüedad y 60 segundos de tolerancia futura.
- Errores y registros sin cuerpos JSON ni secretos.

Los límites son configurables en `.env.example`. El límite global no depende de IP ni confía en cabeceras reenviadas, por lo que no puede eludirse cambiando un encabezado de dirección. A cambio, un ataque puede consumir ese presupuesto compartido. Se reinicia con el proceso y no es un cortafuegos distribuido ni una protección total contra DDoS. Se conserva la configuración de una instancia/un worker.

La versión antigua sin cadena conserva su compatibilidad temporal; no tiene la ventana de antigüedad nueva. Sigue protegida por secreto, ID único, validación, tamaño y frecuencia. Rotar secretos expuestos y restringir acceso es necesario: no existe una clasificación automática fiable de números reales frente a ficticios.

## Archivos y pruebas

- `schemas.py`: contrato 3 y TRANSITION.
- `transition_policy.py`: validación de objetivo, cadena y orden.
- `signal_manager.py`: invalidación y nuevo estado atómicos.
- `tradingview_webhook.py`: autenticación, antigüedad y códigos de conflicto.
- `webhook_security.py`, `config.py`, `main.py`: límites de solicitudes.
- `pine_script/mussett_fx_signals.pine`: único evento atómico.
- `main.py`: consulta, autorización manual y auditoría privada de recuperación.
- `tests/test_atomic_transitions.py`, `tests/test_manual_recovery.py`, `tests/test_postgres.py`, `tests/test_webhook_guards.py`, `tests/conftest.py`: pruebas aisladas.

Las pruebas de concurrencia y rollback se ejecutan contra PostgreSQL usando exclusivamente esquemas temporales. El resto utiliza SQLite temporal. No se envían solicitudes a un Telegram real.
