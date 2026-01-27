# Arduino Motor Bridge — Server (FastAPI) README

Сервер **Arduino Motor Bridge** — это FastAPI-приложение, которое:
- принимает команды по HTTP/WS,
- отправляет команды на Arduino по **USB Serial**,
- отдаёт телеметрию хоста и Arduino,
- поддерживает **до 5 сервоприводов**,
- имеет защиты: **E-STOP** и **server watchdog** (дополнение к watchdog в прошивке).

> Важно: серверный watchdog **не заменяет** watchdog прошивки. Прошивка обязана уметь стопать моторы сама, если сервер/USB отвалились.

---

## 1) Быстрый старт

### 1.1 Установка зависимостей
Рекомендуется Python 3.10+.

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
- fastapi
- uvicorn[standard]
- pyserial
- psutil
- pydantic + pydantic-settings

### 1.2 Конфиг `.env`
Создай `.env` рядом с `run.py`.

Минимальный пример:
```env
# Serial
ARDUINO_BAUD=115200
# если не указать, сервер попробует автопоиск порта
# ARDUINO_PORT=/dev/ttyACM0

# Servo power mode at boot
SERVO_PWR_MODE=ARDUINO

# Servos
SERVO_COUNT=5
SERVO_LIMITS={"1":[10,170],"2":[0,140],"3":[0,180],"4":[20,160],"5":[0,180]}
SERVO_SAFE_POSE={"1":90,"2":90,"3":20,"4":160,"5":90}
SERVO_SLEW_RATE_DPS=120
SERVO_MAX_CMD_HZ=25
SERVO_RATE_LIMIT_MODE=reject

# WebSocket behaviour
WS_PING_INTERVAL=5
WS_PING_TIMEOUT=15
WS_MAX_RATE_HZ=30
WS_STOP_ON_CLOSE=1

# Streaming
STREAM_INTERVAL=1.0

# Safety / E-STOP
ESTOP_ENABLED=1

# Server watchdog (дополнение)
WATCHDOG_ENABLED=1
WATCHDOG_TICK_S=0.2
WATCHDOG_MOTOR_IDLE_S=1.5
WATCHDOG_SERVO_SAFE_ENABLED=0
WATCHDOG_SERVO_IDLE_S=6.0

# Logging profiles (опционально)
LOG_PROFILE=DEFAULT
# LOG_PROFILE=FULL_DEBUG
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

Примерная структура (может отличаться именами файлов, но смысл такой):

```
.
├── run.py                     # точка входа uvicorn
├── .env                       # конфиги (не коммить)
└── server/
    ├── __init__.py            # create_app(), lifespan, регистрация роутеров
    ├── core/
    │   ├── config.py          # Settings (pydantic-settings)
    │   ├── request_id.py      # REQUEST_ID contextvar
    │   └── watchdog.py        # server watchdog loop
    ├── api/
    │   ├── deps.py            # зависимости (например ensure_not_estopped)
    │   └── routes/
    │       ├── health.py
    │       ├── telemetry.py
    │       ├── ws_telemetry.py
    │       ├── motor.py
    │       ├── joystick.py
    │       ├── actions.py
    │       ├── servo.py       # новые servo endpoints (id 1..N)
    │       ├── safety.py      # /estop, /estop/reset
    │       └── ws_joystick.py # WS joystick (с блокировкой при E-STOP)
    ├── schemas/
    │   ├── motor.py
    │   ├── joystick.py
    │   ├── servo.py           # servo multi + batch
    │   └── actions.py
    ├── services/
    │   ├── servo.py           # clamp/limits/slew-rate/rate-limit
    │   ├── joystick.py        # process_joystick()
    │   └── actions.py         # run_action()
    └── serial/
        ├── manager.py         # SerialManager (async wrapper, locks, activity marks)
        └── protocol.py        # ожидания OK/ERR, parse telemetry, etc.
```

---

## 3) Протокол общения с Arduino (кратко)

Сервер шлёт на Arduino команды вида:
- `SetAEngine <speed>`
- `SetBEngine <speed>`
- `SetServo <id> <deg>`
- `ServoPwr ARDUINO|EXTERNAL`
- `TELEM`

Arduino отвечает строками:
- `OK ...`
- `ERR ...`

Подробное ТЗ для прошивки:
- `README_FIRMWARE.md` (техническое)
- `README_FIRMWARE_SIMPLE.md` (простое)

---

## 4) API: HTTP эндпоинты

### 4.1 Health / Telemetry
- `GET /health`  
  Проверка связи: сервер → Arduino `PING`, возвращает статус и режим servo power.

- `GET /telemetry?disk=1&net=1&sensors=1&arduino=1`  
  Хост телеметрия (psutil) + опционально Arduino TELEM.

- `GET /telemetry/arduino`  
  Только Arduino телеметрия (без хоста).

### 4.2 Моторы
- `POST /motor`  
  Body:
  ```json
  {"cmd":"SetAEngine","speed":120}
  ```
  `cmd`: `SetAEngine` | `SetBEngine` | `SetAllEngine`.

### 4.3 Joystick (HTTP)
- `POST /joystick`  
  Body:
  ```json
  {"x":0,"y":120,"deadzone":20,"scale":1.0}
  ```

### 4.4 Сервоприводы (до 5)
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

### 4.5 Режим питания серв
- `GET /servo/power`
- `POST /servo/power`  
  Body:
  ```json
  {"mode":"ARDUINO"}
  ```

### 4.6 Actions (пресеты движений)
- `GET /actions/list`
- `POST /actions/run`  
  Body:
  ```json
  {"action":"forward","power":160,"duration_ms":1000}
  ```

### 4.7 Safety / E-STOP
- `GET /safety/state`
- `POST /estop`  
  Включает E-STOP (сервер блокирует actuators + пытается стопнуть моторы).
- `POST /estop/reset`  
  Снимает блокировку.

**Поведение при E-STOP:**
- HTTP эндпоинты, которые двигают моторы/сервы, получают `423 Locked`.
- `/ws/joystick` перестаёт отправлять мотор-команды и отвечает клиенту `error: estop`.

---

## 5) WebSocket

### 5.1 `GET /ws/telemetry`
Сервер периодически шлёт JSON:
- host snapshot (без диска) + Arduino telem + текущие state поля

Период задаётся `STREAM_INTERVAL`.

### 5.2 `GET /ws/joystick`
Клиент шлёт сообщения джойстика JSON:
```json
{"x":0,"y":120,"deadzone":20,"scale":1.0}
```

Сервер отвечает:
- `type: joy_ack` + применённые моторы и ответы Arduino
- либо `type: error` (например `estop`)

Дополнительно поддерживаются служебные `ping/pong`.

---

## 6) Защиты на стороне сервера

### 6.1 Servo limits + clamp
Для каждого серво можно задать ограничения:
- `SERVO_LIMITS={"1":[10,170],...}`

Даже если клиент отправит 180°, сервер обрежет до max.

### 6.2 Slew-rate (ограничение скорости изменения)
`SERVO_SLEW_RATE_DPS` задаёт максимальную скорость изменения градусов в секунду.
Если приходит резкий скачок — сервер “режет” цель, чтобы двигать плавнее.

### 6.3 Rate-limit по сервам
`SERVO_MAX_CMD_HZ` ограничивает частоту команд для каждого серво.
Если превышение:
- `SERVO_RATE_LIMIT_MODE=reject` → HTTP 429
- `SERVO_RATE_LIMIT_MODE=sleep` → сервер подождёт и отправит

### 6.4 E-STOP
Сервер хранит флаг `app.state.estop`.
Пока он активен — любые actuators блокируются (и WS тоже).

### 6.5 Server watchdog (дополнение)
Фоновая задача проверяет:
- если **нет мотор-команд** дольше `WATCHDOG_MOTOR_IDLE_S` → отправляет `SetAEngine 0`, `SetBEngine 0`
- (опционально) если **нет серво-команд** дольше `WATCHDOG_SERVO_IDLE_S` → ставит safe pose

> Чтобы watchdog не “кормил сам себя”, его команды идут с `mark_activity=False`.

---

## 7) Логи и отладка

Поддерживаются “профили логов” через `LOG_PROFILE`:
- `DEFAULT` — нормальные INFO логи
- `HTTP_DEBUG` — логирование тела HTTP запросов
- `SERIAL_DEBUG` — подробные serial TX/RX
- `FULL_DEBUG` — всё вместе

Если `LOG_PROFILE` не задан и есть TTY — может появиться интерактивное меню выбора (если установлен InquirerPy).

Полезные env:
- `LOG_LEVEL`
- `LOG_REQUEST_BODY=1`
- `MAX_BODY_PREVIEW`
- `SERIAL_LOG=1`
- `SERIAL_MAX_PREVIEW`

---

## 8) Частые проблемы

### 8.1 Не найден Serial порт
- Укажи `ARDUINO_PORT` в `.env` (Linux: `/dev/ttyACM0` или `/dev/ttyUSB0`, Windows: `COM11`).
- Проверь права:
  - Linux: пользователь должен быть в группе `dialout`, либо запуск от root (не рекомендуется).

### 8.2 Arduino “не отвечает” на команды
- Проверь, что прошивка реально печатает `OK ...` и завершает строкой `\n`.
- Убедись, что baudrate совпадает (`ARDUINO_BAUD`).

### 8.3 WS joystick “зависает”
- Проверь `WS_PING_TIMEOUT`, `WS_PING_INTERVAL`.
- Убедись, что клиент отвечает на ping/pong (или хотя бы регулярно шлёт joystick кадры).

---

## 9) Безопасность (важно)

По умолчанию это “локальный мост” к железу.
Если выставлять в интернет:
- обязательно ставь reverse proxy (Nginx) + TLS
- добавь авторизацию (API key / OAuth / JWT)
- ограничь доступ по IP
- не давай доступ к командам моторов/серв публично

---

## 10) Полезные команды для теста (curl)

### Health
```bash
curl -s http://127.0.0.1:8000/health | jq
```

### Motor
```bash
curl -s -X POST http://127.0.0.1:8000/motor \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"SetAllEngine","speed":120}' | jq
```

### Servo #3 -> 120°
```bash
curl -s -X POST http://127.0.0.1:8000/servo/3 \
  -H 'Content-Type: application/json' \
  -d '{"deg":120}' | jq
```

### E-STOP
```bash
curl -s -X POST http://127.0.0.1:8000/estop | jq
curl -s -X POST http://127.0.0.1:8000/estop/reset | jq
```

---

## 11) Версии README по прошивке

- `README_FIRMWARE.md` — подробное техническое ТЗ
- `README_FIRMWARE_SIMPLE.md` — простое ТЗ

Обе версии нужны, чтобы прошивка и сервер “говорили” на одном языке.
