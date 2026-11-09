# MUSSETT FX SIGNALS

Asistente **informativo** de análisis técnico de EUR/USD. Recibe indicadores calculados en TradingView, conserva señales y eventos en PostgreSQL y envía notificaciones a un chat autorizado de Telegram. **No ejecuta operaciones**, no se conecta a EO Broker y nunca solicita credenciales bancarias ni del bróker.

## Fase 2

**Actualización de atomicidad:** el Pine actual utiliza protocolo 3 y una sola petición `TRANSITION` para invalidar el ID anterior y guardar el nuevo estado. Consulta **`TRANSICIONES_ATOMICAS.md`** para el JSON completo, políticas de rechazo, compatibilidad, seguridad y límites. No se modificaron tablas ni se eliminaron datos. Al actualizar el indicador, recrea las alertas existentes.

**Recuperación al reiniciar:** Pine no puede consultar el ID almacenado en el backend. El indicador nuevo establece una línea base sin alertar, espera 225 segundos (sin alterar el TTL de 220 segundos), no reenvía un BUY/SELL estable ni genera WAIT inicial, y abre una cadena de ejecución nueva cuando se observa un cambio real. Véase `TRANSICIONES_ATOMICAS.md`.

La auditoría, las rutas reales, los nuevos campos, las limitaciones de verificación y los pasos pendientes están en **`AUDITORIA_FASE2.md`**. El indicador ahora exige velas estándar de **1 minuto**, valida orientación/separación/expansión GMMA y publica solo al cierre. Las configuraciones intrabar se muestran como PROVISIONAL; no se notifican como confirmadas.

La URL de desarrollo de Replit **no** debe usarse como endpoint permanente de TradingView. Aún necesitas el dominio HTTPS real de Render. No consideres la integración conectada hasta correlacionar una alerta auténtica con los registros del servidor.

## Estado de las conexiones

- El código y las pruebas se pueden ejecutar independientemente de TradingView.
- Una respuesta correcta de `getMe` confirma únicamente que Telegram reconoce la credencial. **No confirma entrega al chat**.
- La entrega real se comprueba con `POST /telegram/test` después de configurar el chat.
- TradingView solo queda conectado cuando una alerta real llega a este servidor y se confirma en el historial.
- Este repositorio incluye preparación para GitHub y Render. No crea cuentas, no contrata planes ni publica automáticamente.
- El Pine Script está en `pine_script/mussett_fx_signals.pine`. Su compilación y la configuración de alertas deben verificarse en el editor de TradingView.

## 1. Requisitos

- Python 3.11 o superior.
- PostgreSQL accesible mediante `DATABASE_URL`.
- Un bot de Telegram creado en BotFather.
- Una cuenta de TradingView con acceso a alertas webhook. Comprueba las condiciones actuales de tu plan.
- Para alojarlo en Render: cuenta de GitHub, cuenta de Render, un servicio web de pago que no se suspenda por inactividad y PostgreSQL persistente. Confirma precios y disponibilidad antes de aceptar la creación de servicios.

## 2. Seguridad y configuración

Si un token se ha publicado en un chat, repositorio o captura, **revócalo en BotFather y reemplázalo**. Nunca lo copies al código, README, Pine Script, logs ni URLs que compartas.

Configura estas variables en el gestor de secretos del alojamiento. Para ejecución local también puedes copiar `.env.example` a `.env`, que está excluido de Git:

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Conexión PostgreSQL; Render la enlaza desde la base de datos del Blueprint. |
| `TELEGRAM_BOT_TOKEN` | Token privado del bot. |
| `TELEGRAM_CHAT_ID` | ID del único chat autorizado. Se recomienda un chat privado. |
| `WEBHOOK_SECRET` | Secreto exclusivo de TradingView, de 32–200 caracteres URL-safe. |
| `API_ACCESS_KEY` | Clave independiente para consultar el historial y ejecutar pruebas privadas. |
| `TELEGRAM_POLLING_ENABLED` | `true` para recibir comandos mediante long polling. |
| `WORKER_ENABLED` | `true` para procesar caducidades y notificaciones. |
| `WORKER_INTERVAL` | Intervalo de revisión, por defecto 1 segundo. |

Genera **dos valores diferentes**, uno para cada secreto de acceso, utilizando un gestor de contraseñas. Si usas Python localmente:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Este comando imprime un **nuevo secreto**, no el token del bot. Guárdalo directamente en el gestor de secretos y no lo compartas en chats ni capturas. En Render el Blueprint genera ambos secretos automáticamente; copia únicamente `WEBHOOK_SECRET` a la configuración privada de tu indicador de TradingView.

Si falta `WEBHOOK_SECRET`, el webhook responde `503`: no admite alertas sin protección. Si falta `API_ACCESS_KEY`, los endpoints privados responden `503`. Si falta el token o el chat, no se envían notificaciones ni se simula una conexión.

## 3. Arranque

En un entorno Python estándar:

```bash
pip install -r requirements.txt
python initialize.py
uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
```

En este proyecto, las dependencias se administran con las herramientas del entorno y el servidor arranca con el flujo de ejecución del API. `python initialize.py` es un paso explícito de preparación: crea únicamente tablas que falten y el estado inicial, sin borrar señales existentes. El servidor **no ejecuta DDL al arrancar**.

`database.py` contiene la base de SQLAlchemy y la construcción de conexiones. Para la inicialización completa usa `initialize.py`, no solo `database.py`.

La documentación interactiva está en `/api/docs`. Puedes consultar el esquema en `/api/openapi.json`.

### Endpoints

| Método y ruta | Protección | Descripción |
|---|---|---|
| `GET /health` | Pública | Estado del servidor y disponibilidad de PostgreSQL; `503` si falla la DB. |
| `GET /status` | `X-API-Key` | Pausa, conteos, estado del worker y estado observado de Telegram. |
| `GET /signals/latest` | `X-API-Key` | Última alerta recibida, o `null`. |
| `GET /signals/history` | `X-API-Key` | Historial con `limit` (1–200), `offset` y filtro `direction`. |
| `GET /recovery/scenarios/{scenario_id}?is_test=...` | `X-API-Key` | Consulta el último estado aceptado de una cadena, separado por clasificación. |
| `POST /recovery/scenarios/{scenario_id}` | `X-API-Key` + operador | Autoriza sincronizar o limpiar manualmente el puntero del indicador; registra quién lo intentó y si se autorizó o rechazó. |
| `GET /recovery/audit` | `X-API-Key` | Consulta los motivos y resultados de las recuperaciones manuales. |
| `POST /webhook/tradingview` | `secret` en JSON | Recibe y persiste la alerta; responde `202`, duplicados `409`. |
| `POST /telegram/test` | `X-API-Key` | Envía un mensaje real de prueba al chat configurado. |
| `GET /events` | `X-API-Key` | Eventos de recepción, caducidad, invalidación y recuperación manual. |
| `POST /signals/{signal_id}/entry` | `X-API-Key` | Registra una entrada **hipotética** manual. |
| `GET /signals/{signal_id}/entry` | `X-API-Key` | Consulta su vencimiento independiente de 180 segundos. |

La cabecera de los endpoints privados es `X-API-Key: <API_ACCESS_KEY>`. La clave no se coloca en parámetros de URL. La documentación pública describe el contrato pero no expone el historial.

Las recuperaciones también requieren `X-Recovery-Operator-ID` y `X-Recovery-Operator-Token`. Configura `RECOVERY_OPERATOR_CREDENTIALS` como un objeto JSON que asocie cada identificador individual con un secreto único de al menos 32 caracteres; guarda los tokens en Secrets y no los compartas entre personas. La `X-API-Key` sigue siendo obligatoria y no se ha reemplazado. Los intentos con datos válidos que se rechazan por el estado del escenario aparecen en la auditoría con `outcome: "rejected"`; las acciones autorizadas muestran `outcome: "authorized"`. Las solicitudes sin autenticación individual válida no se registran como acciones de operador.

Ejemplos desde una terminal con variables de entorno configuradas:

```bash
curl "$SERVICE_URL/health"
curl -H "X-API-Key: $API_ACCESS_KEY" "$SERVICE_URL/status"
curl -X POST -H "X-API-Key: $API_ACCESS_KEY" "$SERVICE_URL/telegram/test"
curl -H "X-API-Key: $API_ACCESS_KEY" \
  -H "X-Recovery-Operator-ID: $RECOVERY_OPERATOR_ID" \
  -H "X-Recovery-Operator-Token: $RECOVERY_OPERATOR_TOKEN" \
  "$SERVICE_URL/recovery/audit"
```

Define `SERVICE_URL` con la URL HTTPS real de tu servicio, sin barra final. No actives trazas `curl -v`, `set -x`, ni logging HTTP de depuración al manejar secretos.

## 4. Telegram, paso a paso

1. Crea el bot en BotFather o rota el token expuesto.
2. Guarda el token en `TELEGRAM_BOT_TOKEN`.
3. Abre el bot en Telegram y envíale `/start`.
4. Sin otro proceso consumiendo actualizaciones del mismo bot, ejecuta:

   ```bash
   python scripts/check_telegram.py --discover-chat
   ```

   El script lee el token del entorno, comprueba `getMe` y muestra los IDs de chats con mensajes pendientes sin confirmarlos mediante un offset nuevo. No imprime el token. Mientras falta `TELEGRAM_CHAT_ID`, el servidor no inicia polling, por lo que puedes hacer este paso sin detenerlo. Si ya estaba configurado, detén temporalmente el servidor antes de descubrir chats.

5. Guarda el ID correcto en `TELEGRAM_CHAT_ID` y reinicia el servidor.
6. Ejecuta `POST /telegram/test`. Solo un resultado `sent: true` y la recepción del mensaje confirman el envío al chat.
7. Envía `/help`, `/status`, `/last`, `/pause` y `/resume` desde ese chat.

Los mensajes de otros chats se ignoran. En grupos, cualquier miembro que pueda escribir en el chat autorizado podría usar los comandos: usa un chat privado si necesitas control de un solo usuario.

| Comando | Función |
|---|---|
| `/start` | Presentación y alcance informativo. |
| `/help` | Ayuda y advertencias. |
| `/status` | Estado de las notificaciones y escenarios pendientes. |
| `/last` | Último escenario y estado real actual. |
| `/pause` | Pausa notificaciones; recepción, registros y caducidades continúan. |
| `/resume` | Reanuda solo futuras notificaciones; no reenvía pendientes antiguos. |

Se utiliza long polling, **no un webhook de Telegram**. Si el bot tiene un webhook existente, el programa marca `webhook_conflict` y no lo elimina automáticamente. Resuelve ese conflicto manualmente en la integración que lo instaló. No ejecutes otro consumidor `getUpdates` con el mismo bot.

## 5. TradingView y Pine Script v6

1. Abre un gráfico de **EUR/USD** en temporalidad de **1 minuto**, con velas estándar.
2. En Pine Editor, abre o pega el archivo independiente `pine_script/mussett_fx_signals.pine`.
3. Guarda, compila y añade el indicador al gráfico.
4. Configura `WEBHOOK_SECRET` en los ajustes del indicador y el identificador de alerta. Usa el mismo secreto del servidor. Cada alerta independiente debe tener un identificador diferente.
5. Crea una alerta cuya condición sea el indicador y **“Any alert() function call”**.
6. Activa el webhook y usa la URL HTTPS de tu servidor seguida de `/webhook/tradingview`.
7. El script genera el JSON. No es necesario construir a mano un mensaje que sustituya las llamadas `alert()`.
8. Confirma que `/signals/history` muestra el evento y que Telegram lo entrega si las notificaciones están activas.
9. Si cambias el secreto, código o parámetros, **recrea la alerta**: TradingView guarda una copia de la configuración al crearla.

El indicador usa SMA 200, EMA 14, RSI 14, las doce EMA del GMMA, volumen relativo sobre 20 velas y máximos/mínimos mediante pivotes confirmados. Evalúa únicamente velas cerradas:

- BUY: precio por encima de SMA 200 y EMA 14, impulso EMA 14 ascendente, GMMA con grupos ordenados, separados, orientados y en expansión, RSI entre 50 y 70, estructura creciente y volumen relativo superior al umbral cuando se usa una fuente útil.
- SELL: condiciones simétricas bajistas, RSI entre 30 y 50.
- WAIT: no se cumplen las confirmaciones.

Estas reglas son criterios explícitos, **no una estrategia con rentabilidad o probabilidad validada**. No se generan señales solo por sobrecompra/sobreventa ni por un cruce simple. El volumen Forex depende del proveedor: se comunica su tipo según los metadatos. El volumen de ticks no equivale al volumen total del mercado. Cuando falta volumen útil o el proveedor no identifica su tipo, el filtro se omite explícitamente; no se inventa una confirmación de volumen. Los pivotes necesitan velas posteriores para confirmarse; el indicador no anticipa esos datos.

Solo se emite un escenario nuevo al cambiar el estado. Si BUY o SELL pierde confirmación, se emite una invalidación del escenario anterior y un nuevo WAIT. Las señales opuestas invalidan las pendientes de la **misma temporalidad**. Los datos anteriores no se convierten en recomendaciones ejecutables.

### JSON admitido

```json
{
  "schema_version": 2,
  "is_test": false,
  "secret": "REEMPLAZAR_POR_WEBHOOK_SECRET",
  "symbol": "EURUSD",
  "direction": "BUY",
  "timeframe": "1",
  "price": 1.165,
  "rsi": 55.5,
  "sma200": 1.164,
  "ema14": 1.1651,
  "gmma_status": "BULLISH",
  "gmma_short_spread": 0.021,
  "gmma_long_spread": 0.012,
  "volume_status": "INCREASING",
  "volume_type": "TICK",
  "signal_id": "ejemplo-ficticio-unico",
  "candle_time": "2026-11-09T15:30:00Z",
  "relative_volume": 1.2,
  "trend_status": "BULLISH",
  "structure": "HIGHER_HIGHS_LOWS",
  "signal_status": "CONFIRMED",
  "event_type": "SIGNAL"
}
```

La fase 2 requiere `gmma_short_spread`, `gmma_long_spread`, `trend_status` y `signal_status`. Los spreads son porcentajes del cierre, no pips. `relative_volume`, `structure` y `volume_type` aportan contexto adicional. El contrato anterior sigue admitido sin inventar los campos que falten: el mensaje indica **NO PROPORCIONADO**. Se aceptan `EURUSD` y `EUR/USD`, normalizados a `EURUSD`. Las fechas deben incluir zona horaria. Los precios deben ser positivos, RSI entre 0 y 100 y los estados pertenecer a sus enumeraciones. Los campos desconocidos o contradictorios se rechazan.

El JSON anterior es un ejemplo compatible de versión 2, no la transición del indicador actual. En versión 3 se añade `scenario_id` y se usa `TRANSITION` con `invalidates_signal_id` para reemplazar una configuración vigente en una sola transacción.

Para una invalidación explícita, usa un nuevo `signal_id`, `event_type: "INVALIDATION"`, `signal_status: "INVALIDATED"` e `invalidates_signal_id` con el ID existente y la misma temporalidad. Se admite `direction: "INVALIDATION"` y se normaliza internamente a WAIT con tipo INVALIDATION. Envía también los demás campos requeridos del contrato. Si el escenario ya expiró o ya se invalidó, no se reactiva.

**`is_test` es obligatorio en todas las versiones del contrato** y debe ser un booleano JSON: `true` para pruebas ficticias o `false` para alertas no ficticias. No se aceptan cadenas como `"false"`, números ni valores omitidos: el webhook responde `422` sin guardar señales ni encolar avisos. La clasificación del emisor no certifica por sí sola procedencia de TradingView.

Con `is_test: true`, los avisos y el resumen de `/last` conservan la advertencia **PRUEBA FICTICIA · NO ES UNA SEÑAL REAL** y no podrán invalidar escenarios reales. El indicador actualizado envía explícitamente `is_test: false`. Si ya creaste una alerta en TradingView con el código anterior, **actualiza el indicador y recrea la alerta** para que incluya este campo.

### Recuperar el estado local del indicador

TradingView no comunica al script si el webhook fue aceptado. Si se pierde una entrega, el indicador se reinicia o recibe `409/422`, no reenvíes una transición antigua ni intentes renovar su ventana. Con `X-API-Key`, consulta `GET /recovery/scenarios/{scenario_id}?is_test=false` (usa `true` para pruebas). Si el último BUY/SELL sigue vigente, autoriza `SYNC_LOCAL_POINTER` en `POST /recovery/scenarios/{scenario_id}` con el `signal_id` aceptado exacto y copia los valores devueltos de `signal_id`, `scenario_id` y dirección a los campos «Recuperación manual». Si no hay un BUY/SELL vigente, autoriza `CLEAR_LOCAL_POINTER` y deja vacíos los tres campos. El borrado se bloquea mientras exista un BUY/SELL pendiente.

Cada acción válida exige un motivo y queda en `GET /recovery/audit`; no cambia señales ni vencimientos y no manda mensajes a Telegram. Después de un reinicio se debe repetir la consulta/autorización. Sin una sincronización manual, el indicador espera 225 segundos antes de publicar desde una ejecución nueva; después emite solo ante un cambio confirmado. Un `409` pide reconciliar el estado; un `422`, corregir los datos o la clasificación `is_test`. Si la vela es demasiado antigua, espera un cierre confirmado nuevo. Solo un nuevo evento aceptado obtiene su plazo original de 220 segundos; no hay renovación automática.

## 6. Ventana de revisión y entrega

- Cada escenario nuevo tiene `received_at` UTC y `expires_at = received_at + 220 segundos`.
- No hay llamadas a Telegram dentro del webhook: se confirma la transacción y se responde. El worker envía el mensaje inicial desde una cola persistente.
- El worker revisa la base de datos cada segundo aproximadamente. El vencimiento almacenado es exacto; **la recepción del aviso en Telegram puede retrasarse** por red, carga o indisponibilidad.
- La ventana no se renueva por reintentos, duplicados, reinicios ni nuevos eventos.
- Los escenarios invalidados no envían después un aviso de caducidad.
- Si el servidor cae, los pendientes se recuperan de PostgreSQL al volver a arrancar. No se envía como vigente un mensaje inicial que ya venció.
- `/pause` omite las notificaciones de señales pero conserva todos los registros. Un envío que ya estaba en curso puede terminar justo después de la pausa.
- No se intenta calcular resultados de operaciones, beneficios, aciertos ni probabilidades.

### Duplicados y límites de Telegram

`signal_id`, las claves de notificación y los IDs de actualización de Telegram son únicos. PostgreSQL serializa la ingestión y el worker usa un bloqueo asesor para que no haya dos consumidores activos en la misma base de datos.

Telegram `sendMessage` no ofrece una clave de idempotencia. Un timeout **después** de enviar puede significar que el mensaje llegó aunque no se recibió respuesta. Para evitar duplicados, estos envíos se marcan `uncertain` y **no se reenvían automáticamente**. Lo mismo ocurre si el proceso se reinicia mientras una entrega estaba marcada `dispatching`. Esto prioriza no duplicar mensajes, pero puede omitir una entrega incierta. Consulta los contadores en `/status` y verifica el chat manualmente.

Los errores de conexión antes del envío y las respuestas `429` se reintentan, como máximo cinco veces, respetando la espera indicada. Los errores definitivos se marcan `failed`. El estado `connected` es el último resultado observado, no una promesa de disponibilidad futura.

### Entrada hipotética manual

```json
{"entered_at": "2026-11-09T15:31:00Z"}
```

Envíalo a `POST /signals/{signal_id}/entry` con `X-API-Key`. Se registra `expires_at = entered_at + 180 segundos`, separado de la revisión de 220 segundos. No admite fechas futuras, WAIT, eventos de invalidación ni una segunda entrada para el mismo escenario. No abre una operación ni envía una orden a un bróker.

## 7. Pruebas

```bash
python -m pytest -q
```

Para resultados de cada caso en un informe independiente:

```bash
python -m pytest -v --junitxml=generated-artifacts/phase2-tests.xml -rA
python scripts/build_test_report.py generated-artifacts/phase2-tests.xml generated-artifacts/phase2-tests.html
```

Las pruebas usan exclusivamente datos ficticios. La mayoría utilizan SQLite temporal para aislar los casos; las pruebas PostgreSQL crean un esquema temporal aleatorio en la base de desarrollo, comprueban duplicados concurrentes y recuperación de vencimientos y eliminan únicamente ese esquema temporal.

**Nunca ejecutes la batería con una conexión de producción.** `DATABASE_URL` debe apuntar a desarrollo o una base de pruebas.

Incluyen BUY, SELL, WAIT, secreto incorrecto, validaciones, símbolos prohibidos, duplicados, invalidación explícita/opuesta, límite exacto de 220 segundos, recuperación desde persistencia, comandos, pausa/reanudación, autorización, mensajes Telegram simulados, errores de red, rate limit y separación del reloj hipotético.

Las pruebas no conectan a un bróker, no colocan operaciones y no envían mensajes reales. Para comprobar solo la credencial real de Telegram:

```bash
python scripts/check_telegram.py
```

## 8. GitHub

1. Crea un repositorio privado si vas a almacenar configuraciones no públicas.
2. Revisa `.gitignore`: `.env`, dependencias, bases temporales y adjuntos están excluidos.
3. No subas valores privados. `.env.example` contiene solo nombres y espacios vacíos.
4. Usa el control de versiones del editor para conectar tu cuenta de GitHub y enviar los archivos, o configura Git en una terminal autenticada:

   ```bash
   git remote add origin <URL_DE_TU_REPOSITORIO>
   git add .
   git commit -m "Implement MUSSETT FX SIGNALS"
   git push -u origin main
   ```

   Si ya existe `origin`, comprueba primero su destino; no lo reemplaces a ciegas. No pongas tokens de GitHub en la URL del repositorio.
5. Verifica en GitHub que no se ha subido ningún secreto antes de conectar Render.

## 9. Render, servicio permanente

1. Conecta a Render el repositorio que contiene `render.yaml`.
2. Selecciona **New → Blueprint** y revisa los recursos que propone.
3. El Blueprint solicita un web service **Starter** y PostgreSQL **basic-256mb**. Son recursos de pago: confirma las condiciones actuales en Render antes de aceptar.
4. Introduce `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` cuando Render lo solicite. No se copian automáticamente desde otros entornos.
5. Render enlaza `DATABASE_URL` y genera `WEBHOOK_SECRET` y `API_ACCESS_KEY`.
6. Build: `pip install -r requirements.txt`.
7. Pre-deploy: `python initialize.py`. Este paso es para PostgreSQL externo de Render.
8. Inicio: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`.
9. Espera a que `GET /health` confirme servidor y base de datos.
10. Prueba Telegram y configura TradingView con la URL pública HTTPS real de Render.
11. Revisa `/status`, el historial y los eventos antes de considerar la conexión operativa.

No se utilizan pings artificiales. Mantén **una instancia y un worker de Uvicorn** para esta versión. El bloqueo de PostgreSQL evita consumo duplicado durante solapamientos de despliegue, pero no convierte esta versión en una arquitectura de alta disponibilidad. Las tablas se crean de forma idempotente; futuras modificaciones de columnas necesitarán migraciones explícitas, no solo `create_all`.

En un alojamiento que gestione automáticamente la publicación de esquemas, sigue su flujo de publicación: no conviertas `initialize.py` en una migración automática al arrancar.

Referencias oficiales:
- https://render.com/docs/blueprint-spec
- https://render.com/docs/deploys
- https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
- https://core.telegram.org/bots/api

## Estructura

```text
main.py                    # FastAPI, ciclo de vida y endpoints privados
config.py                  # Variables de entorno y secretos tipados
database.py                # Motor y sesiones SQLAlchemy
models.py                  # Señales, eventos, estado y cola persistente
schemas.py                 # Validación del contrato
telegram_bot.py            # Transporte independiente HTTPX
tradingview_webhook.py      # Autenticación y recepción
signal_manager.py          # Ingestión, invalidación y mensajes
expiration_manager.py      # Caducidad, outbox y comandos
initialize.py              # Inicialización explícita
pine_script/
  mussett_fx_signals.pine   # Indicador independiente Pine Script v6
scripts/
  check_telegram.py         # Comprobación segura y descubrimiento de chat
  verify_webhook.py         # Prueba HTTPS ficticia en el dominio real de Render
  build_test_report.py      # Informe individual de resultados pytest
tests/                     # Pruebas ficticias aisladas
requirements.txt
.env.example
render.yaml
```

**Este sistema es informativo. Requiere revisión humana y no garantiza beneficios.**
