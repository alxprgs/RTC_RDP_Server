# Arduino Motor Bridge — Server (FastAPI)

Сервер **Arduino Motor Bridge** — это FastAPI-приложение, которое:
- принимает команды по HTTP и WebSocket,
- отправляет команды на Arduino/робота по **USB Serial**,
- отдаёт телеметрию **хоста** (psutil) и **Arduino** (через `TELEM`),
- поддерживает **до 5 сервоприводов** (на стороне API/валидации; прошивка должна это реализовать),
- имеет защиты: **E‑STOP** и **server watchdog** (дополнение к watchdog в прошивке),
- умеет проверять **версию сервера** и **обновления на GitHub** (если есть интернет),
- умеет проверять **версию прошивки** и **поддерживаемые команды** робота (CAPS/commands).

> Важно: серверный watchdog **не заменяет** watchdog прошивки. Прошивка обязана уметь стопать моторы сама, если сервер/USB отвалились.

---

## 1) Быстрый старт

### 1.1 Установка зависимостей
Рекомендуется Python **3.10+**.

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -U pip
pip install -r requirements.txt
```

Минимально нужны:
- `fastapi`
- `uvicorn[standard]`
- `pyserial`
- `psutil`
- `pydantic` + `pydantic-settings`
- (опционально) `InquirerPy` — интерактивное меню лог-профилей при запуске

### 1.2 Конфиг `.env`
Создай `.env` рядом с `run.py`. Ниже — **пример**, подстрой под себя.

```env
# -------------------------
# Serial
# -------------------------
ARDUINO_BAUD=115200
# если не указать, сервер попробует автопоиск порта
# ARDUINO_PORT=/dev/ttyACM0

# Servo power mode at boot (команда в прошивку на старте)
SERVO_PWR_MODE=ARDUINO

# -------------------------
# Servos (серверная валидация/защиты)
# -------------------------
SERVO_COUNT=5
SERVO_LIMITS={"1":[10,170],"2":[0,140],"3":[0,180],"4":[20,160],"5":[0,180]}
SERVO_SAFE_POSE={"1":90,"2":90,"3":20,"4":160,"5":90}

# Плавность (опционально)
SERVO_SLEW_RATE_DPS=120
# Ограничение частоты команд на каждый серво
SERVO_MAX_CMD_HZ=25
# Что делать при превышении частоты: reject|sleep
SERVO_RATE_LIMIT_MODE=reject

# -------------------------
# WebSocket behaviour
# -------------------------
WS_PING_INTERVAL=5
WS_PING_TIMEOUT=15
WS_MAX_RATE_HZ=30
WS_STOP_ON_CLOSE=1

# Streaming interval for /ws/telemetry
STREAM_INTERVAL=1.0

# -------------------------
# Safety / E-STOP
# -------------------------
ESTOP_ENABLED=1

# -------------------------
# Server watchdog (дополнение к прошивочному)
# -------------------------
WATCHDOG_ENABLED=1
WATCHDOG_TICK_S=0.2
WATCHDOG_MOTOR_IDLE_S=1.5
WATCHDOG_SERVO_SAFE_ENABLED=0
WATCHDOG_SERVO_IDLE_S=6.0

# -------------------------
# Firmware probe (CAPS + FWVER/VERSION/VER)
# -------------------------
DEVICE_PROBE_ON_STARTUP=1
DEVICE_PROBE_TIMEOUT_S=2.5
# Разрешить “жёсткую” проверку supported commands из CAPS
DEVICE_ENFORCE_COMMANDS=1

# -------------------------
# Update check (GitHub)
# -------------------------
UPDATE_CHECK_ENABLED=1
# repo в формате owner/name
GITHUB_REPO=alxprgs/RTC_RDP_Server
# ветка для сравнения, если нет релизов/тегов
GITHUB_BRANCH=main
# как часто авто-перепроверять (сек), 0 = только вручную
UPDATE_CHECK_INTERVAL_S=3600

# -------------------------
# Версия приложения (вшивается при сборке/деплое)
# -------------------------
APP_VERSION=0.0.0
GIT_SHA=
BUILD_TIME_UTC=

# -------------------------
# Logging profiles (опционально)
# -------------------------
LOG_PROFILE=DEFAULT
# LOG_PROFILE=FULL_DEBUG
# LOG_LEVEL=INFO
# LOG_REQUEST_BODY=0
# SERIAL_LOG=0
```

### 1.3 Запуск
```bash
python run.py
```

или напрямую:
```bash
uvicorn run:app --host 0.0.0.0 --port 8000
```

---

## 2) Архитектура проекта (типовая структура)

Примерная структура (имена файлов могут отличаться, но смысл такой):

```
.
├── run.py                     # точка входа uvicorn
├── .env                       # конфиги (не коммить)
└── server/
    ├── __init__.py            # create_app(), lifespan, регистрация роутеров
    ├── core/
    │   ├── config.py          # Settings (pydantic-settings)
    │   ├── request_id.py      # REQUEST_ID contextvar
    │   ├── estop.py           # состояние E-STOP + утилиты
    │   ├── watchdog.py        # server watchdog loop
    │   ├── version.py         # версия сервера, update-check (GitHub)
    │   └── device_probe.py    # probe робота: CAPS/FWVER + кэш
    ├── api/
    │   ├── deps.py            # зависимости (ensure_not_estopped, ensure_command_supported)
    │   └── routes/
    │       ├── health.py
    │       ├── telemetry.py
    │       ├── ws_telemetry.py
    │       ├── motor.py
    │       ├── joystick.py
    │       ├── actions.py
    │       ├── servo.py       # servo endpoints (id 1..N, batch)
    │       ├── safety.py      # /estop, /estop/reset, /safety/state
    │       ├── device.py      # /device, /device/refresh
    │       ├── version.py     # /version, /version/check
    │       └── ws_joystick.py # WS joystick (с блокировкой при E-STOP)
    ├── schemas/
    │   ├── motor.py
    │   ├── joystick.py
    │   ├── servo.py
    │   ├── actions.py
    │   └── device.py          # CAPS/FWVER схемы (кэш)
    ├── services/
    │   ├── servo.py           # clamp/limits/slew-rate/rate-limit
    │   ├── joystick.py        # process_joystick()
    │   ├── actions.py         # run_action()
    │   └── system_snapshot.py # get_system_snapshot()
    └── serial/
        ├── manager.py         # SerialManager (async wrapper, locks, RX buffer)
        └── protocol.py        # ожидания OK/ERR, parse telemetry, infer OK tokens
```

---

## 3) Протокол общения с Arduino (кратко)

Сервер шлёт на Arduino команды вида:
- `PING`
- `CAPS`
- `TELEM`
- `FWVER` / `VERSION` / `VER` (проверка версии прошивки)
- `SetAEngine <speed>`
- `SetBEngine <speed>`
- `SetAllEngine <speed>`
- `SetServo <id> <deg>`
- `SetServos {json}`
- `ServoPwr ARDUINO|EXTERNAL`
- `EStop` / `EStop RESET` (если вы решите делать E-STOP на прошивке тоже)

Arduino отвечает строками:
- `OK ...`
- `ERR ...`

Подробное ТЗ для прошивки:
- `README_FIRMWARE.md` (техническое)
- `README_FIRMWARE_SIMPLE.md` (простое)

---

## 4) Firmware probe (CAPS + FWVER) — что это и зачем

### 4.1 Что делает probe
Сервер умеет “спросить” у робота:
- `CAPS` → capabilities + список `commands` (что прошивка реально поддерживает)
- `FWVER`/`VERSION`/`VER` → версия прошивки (для отображения и диагностики)

### 4.2 Зачем нужен список commands
Если прошивка отдаёт в CAPS поле `commands`, сервер может:
- **не отправлять** неподдерживаемые команды,
- **возвращать клиенту** понятную ошибку (HTTP **501 Not Implemented**),
- показывать UI/телеметрию “какие фичи доступны”.

Если `commands` нет — сервер работает в режиме “best effort” (на свой страх и риск).

### 4.3 Эндпоинты probe
- `GET /device` — вернуть текущий кэш (CAPS/firmware version/last seen)
- `POST /device/refresh` — принудительно обновить кэш (пингнуть Arduino)

---

## 5) Версия сервера + проверка обновлений на GitHub

### 5.1 Что умеем
Если есть интернет, сервер может сверять:
- “какая версия сейчас запущена” (`APP_VERSION`, `GIT_SHA`, `BUILD_TIME_UTC`)
- “есть ли обновления на GitHub” для репозитория `GITHUB_REPO`

### 5.2 Как сверяем (идея)
Самый простой и понятный вариант:
- взять “latest release” GitHub (если есть релизы),
- иначе сравнить текущий `GIT_SHA` с `GITHUB_BRANCH` (последний коммит ветки).

Если интернет/ GitHub недоступен — сервер возвращает статус `unavailable`, без падения.

### 5.3 Эндпоинты версии
- `GET /version` — текущая версия сервера + кэш результата update-check
- `POST /version/check` — принудительно проверить GitHub сейчас

---

## 6) API: HTTP эндпоинты (подробнее)

### 6.1 Health / Telemetry
- `GET /health`  
  Проверка связи: сервер → Arduino `PING`, возвращает статус и режим servo power.

- `GET /telemetry?disk=1&net=1&sensors=1&arduino=1`  
  Хост-телеметрия (psutil) + опционально Arduino TELEM.

- `GET /telemetry/arduino`  
  Только Arduino телеметрия (без хоста).

### 6.2 Моторы
- `POST /motor`  
  Body:
  ```json
  {"cmd":"SetAEngine","speed":120}
  ```
  `cmd`: `SetAEngine` | `SetBEngine` | `SetAllEngine`.

### 6.3 Joystick (HTTP)
- `POST /joystick`  
  Body:
  ```json
  {"x":0,"y":120,"deadzone":20,"scale":1.0}
  ```

### 6.4 Сервоприводы (до 5)
- `GET /servo/capabilities`  
  Кол-во серв, лимиты, safe pose, slew-rate, rate-limit.

- `GET /servo/state`  
  Последние выставленные значения (кэш сервера).

- `POST /servo/{id}`  
  Body:
  ```json
  {"deg":90}
  ```

- `POST /servo/batch`  
  Body:
  ```json
  {"items":[{"id":1,"deg":90},{"id":2,"deg":20}]}
  ```

- `POST /servo/center`  
  Центрует все сервы (или safe pose).

- Совместимость (шорткаты):
  - `POST /servo/a` (id=1)
  - `POST /servo/b` (id=2)
  - `POST /servo/all` (на все servo_count)

### 6.5 Режим питания серв
- `GET /servo/power`
- `POST /servo/power`  
  Body:
  ```json
  {"mode":"ARDUINO"}
  ```

### 6.6 Actions (пресеты движений)
- `GET /actions/list`
- `POST /actions/run`  
  Body:
  ```json
  {"action":"forward","power":160,"duration_ms":1000}
  ```

### 6.7 Safety / E-STOP
- `GET /safety/state`
- `POST /estop`  
  Включает E-STOP (сервер блокирует actuators + пытается стопнуть моторы).

- `POST /estop/reset`  
  Снимает блокировку.

**Поведение при E-STOP:**
- HTTP эндпоинты, которые двигают моторы/сервы, получают `423 Locked`.
- `/ws/joystick` перестаёт отправлять мотор-команды и отвечает клиенту `error: estop`.

---

## 7) WebSocket

### 7.1 `GET /ws/telemetry`
Сервер периодически шлёт JSON:
- host snapshot (без диска) + Arduino telem + текущие state поля

Период задаётся `STREAM_INTERVAL`.

### 7.2 `GET /ws/joystick`
Клиент шлёт сообщения джойстика JSON:
```json
{"x":0,"y":120,"deadzone":20,"scale":1.0}
```

Сервер отвечает:
- `type: joy_ack` + применённые моторы и ответы Arduino
- либо `type: error` (например `estop`)

Дополнительно поддерживаются служебные `ping/pong`.

---

## 8) Защиты на стороне сервера

### 8.1 Servo limits + clamp
Для каждого серво можно задать ограничения:
- `SERVO_LIMITS={"1":[10,170],...}`

Даже если клиент отправит 180°, сервер обрежет до max.

### 8.2 Slew-rate (ограничение скорости изменения)
`SERVO_SLEW_RATE_DPS` задаёт максимальную скорость изменения градусов в секунду.
Если приходит резкий скачок — сервер “режет” цель, чтобы двигать плавнее.

### 8.3 Rate-limit по сервам
`SERVO_MAX_CMD_HZ` ограничивает частоту команд для каждого серво.
Если превышение:
- `SERVO_RATE_LIMIT_MODE=reject` → HTTP 429
- `SERVO_RATE_LIMIT_MODE=sleep` → сервер подождёт и отправит

### 8.4 E-STOP
Сервер хранит флаг `app.state.estop`.
Пока он активен — любые actuators блокируются (и WS тоже).

### 8.5 Server watchdog (дополнение)
Фоновая задача проверяет:
- если **нет мотор-команд** дольше `WATCHDOG_MOTOR_IDLE_S` → отправляет `SetAEngine 0`, `SetBEngine 0`
- (опционально) если **нет серво-команд** дольше `WATCHDOG_SERVO_IDLE_S` → ставит safe pose

> Чтобы watchdog не “кормил сам себя”, его команды должны идти с `mark_activity=False`.

---

## 9) Логи и отладка

Поддерживаются “профили логов” через `LOG_PROFILE`:
- `DEFAULT` — нормальные INFO логи
- `HTTP_DEBUG` — логирование тела HTTP запросов
- `SERIAL_DEBUG` — подробные serial TX/RX
- `FULL_DEBUG` — всё вместе
- `QUIET` — минимум

Если `LOG_PROFILE` не задан и есть TTY — может появиться интерактивное меню выбора (если установлен InquirerPy).

Полезные env:
- `LOG_LEVEL`
- `LOG_REQUEST_BODY=1`
- `MAX_BODY_PREVIEW`
- `SERIAL_LOG=1`
- `SERIAL_MAX_PREVIEW`

---

## 10) Частые проблемы

### 10.1 Не найден Serial порт
- Укажи `ARDUINO_PORT` в `.env` (Linux: `/dev/ttyACM0` или `/dev/ttyUSB0`, Windows: `COM11`).
- Проверь права:
  - Linux: пользователь должен быть в группе `dialout`, либо запуск от root (не рекомендуется).

### 10.2 Arduino “не отвечает” на команды
- Проверь, что прошивка реально печатает `OK ...` и завершает строкой `\n`.
- Убедись, что baudrate совпадает (`ARDUINO_BAUD`).
- Если включён `DEVICE_PROBE_ON_STARTUP=1`, а прошивка не поддерживает `CAPS`/`FWVER`, отключи probe или добавь эти команды в прошивку.

### 10.3 WS joystick “зависает”
- Проверь `WS_PING_TIMEOUT`, `WS_PING_INTERVAL`.
- Убедись, что клиент отвечает на ping/pong (или хотя бы регулярно шлёт joystick кадры).

---

## 11) Безопасность (важно)

По умолчанию это “локальный мост” к железу.
Если выставлять в интернет:
- обязательно ставь reverse proxy (Nginx) + TLS
- добавь авторизацию (API key / OAuth / JWT)
- ограничь доступ по IP
- не давай доступ к командам моторов/серв публично

---

## 12) Полезные команды для теста (curl)

### Health
```bash
curl -s http://127.0.0.1:8000/health | jq
```

### Device (CAPS/FWVER)
```bash
curl -s http://127.0.0.1:8000/device | jq
curl -s -X POST http://127.0.0.1:8000/device/refresh | jq
```

### Version / update-check
```bash
curl -s http://127.0.0.1:8000/version | jq
curl -s -X POST http://127.0.0.1:8000/version/check | jq
```

### Motor
```bash
curl -s -X POST http://127.0.0.1:8000/motor   -H 'Content-Type: application/json'   -d '{"cmd":"SetAllEngine","speed":120}' | jq
```

### Servo #3 -> 120°
```bash
curl -s -X POST http://127.0.0.1:8000/servo/3   -H 'Content-Type: application/json'   -d '{"deg":120}' | jq
```

### E-STOP
```bash
curl -s -X POST http://127.0.0.1:8000/estop | jq
curl -s -X POST http://127.0.0.1:8000/estop/reset | jq
```

---

## 13) README по прошивке

- `README_FIRMWARE.md` — подробное техническое ТЗ
- `README_FIRMWARE_SIMPLE.md` — простое ТЗ

Обе версии нужны, чтобы прошивка и сервер “говорили” на одном языке.
