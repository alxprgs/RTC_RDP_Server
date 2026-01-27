# Arduino Motor Bridge — прошивка Arduino (простое ТЗ)

Это простое описание того, **что должна уметь новая прошивка Arduino**.

Сервер (FastAPI) будет слать команды по USB‑Serial, а Arduino должна:
- управлять **2 моторами** (A и B)
- управлять **до 5 сервоприводов**
- уметь переключать режим питания серв (**ARDUINO** / **EXTERNAL**)
- отдавать телеметрию в **JSON**
- иметь защиты: **Watchdog** и **E‑STOP**
- уметь сказать **версию прошивки** и список **поддерживаемых команд** (это нужно серверу)

---

## 1) Как общаемся (Serial)

- Каждая команда — это **одна строка текста**, заканчивается `\n`.
- Arduino отвечает **тоже одной строкой**, заканчивается `\n`.

### Формат ответов
- Успех: начинается с `OK ...`
- Ошибка: начинается с `ERR ...`

**Важно:** Arduino **всегда** должна отвечать. Никакого молчания.

---

## 2) Команды, которые должны быть (обязательно)

### 2.1 Проверка связи
**Команда**
```
PING
```
**Ответ**
```
OK PONG
```

### 2.2 Сказать “что ты умеешь” (Capabilities)
**Команда**
```
CAPS
```

**Ответ**: `OK CAPS <json>`

Пример:
```
OK CAPS {
  "servo_count":5,
  "servo_deg_min":0,
  "servo_deg_max":180,
  "supports_batch":true,
  "supports_detach":true,
  "supports_estop":true,
  "commands":["PING","CAPS","TELEM","FWVER","SetAEngine","SetBEngine","SetAllEngine","SetServo","SetServos","ServoCenter","ServoPwr","EStop"]
}
```

Что важно в CAPS:
- `servo_count` (например 5)
- `servo_deg_min`, `servo_deg_max` (обычно 0..180)
- флаги `supports_*` (можно true/false)
- **`commands`** — список команд, которые реально поддерживает прошивка  
  (сервер будет на это смотреть и может блокировать неподдерживаемые вещи)

> Если `commands` не сделаете — сервер будет работать “по старинке”, но лучше сделать.

### 2.3 Версия прошивки (Firmware version)
Нужно для сервера, чтобы показывать “какая прошивка стоит”.

Поддержи **хотя бы одну** из команд ниже (лучше все 3, но можно одну):

```
FWVER
VERSION
VER
```

**Ответы (пример)**
```
OK FWVER 1.2.0
```
или
```
OK VERSION {"version":"1.2.0","git":"abc123"}
```

---

## 3) Моторы (A и B)

Arduino должна принимать команды:

```
SetAEngine <speed>
SetBEngine <speed>
SetAllEngine <speed>
```

- `<speed>` это число от `-255` до `255`
  - `255` — максимум вперёд
  - `-255` — максимум назад
  - `0` — стоп

Ответы (пример):
```
OK SETAENGINE
OK SETBENGINE
OK SETALLENGINE
```

Если аргумент неправильный — отвечаем:
```
ERR BAD_ARGS ...
```

Если включён E‑STOP — отвечаем:
```
ERR ESTOP ...
```

---

## 4) Сервоприводы (до 5 штук)

### 4.1 Поставить один сервопривод
Команда:
```
SetServo <id> <deg>
```
- `<id>`: 1..5
- `<deg>`: 0..180

Пример:
```
SetServo 3 120
```

Ответ:
```
OK SETSERVO id=3 deg=120
```

### 4.2 Поставить сразу несколько серв (очень желательно)
Команда:
```
SetServos {"items":[{"id":1,"deg":90},{"id":2,"deg":20},{"id":5,"deg":160}]}
```

Ответ:
```
OK SETSERVOS {"applied":3}
```

Если batch пока не сделали — можно сначала без него, но лучше реализовать.

### 4.3 Центрировать сервы (safe pose)
Команда:
```
ServoCenter
```

Ответ:
```
OK SERVO_CENTER
```

Что делает: ставит все сервы в “безопасное положение”, например 90°.

### 4.4 Attach / Detach (опционально, но полезно)
Иногда сервы “дрожат” и греются. Тогда можно отключать управление.

Команды:
```
ServoAttach <id>
ServoDetach <id>
ServoDetachAll
```

Ответы:
```
OK SERVO_ATTACH id=1
OK SERVO_DETACH id=1
OK SERVO_DETACH_ALL
```

---

## 5) Режим питания серв (ARDUINO / EXTERNAL)

Команды:
```
ServoPwr ARDUINO
ServoPwr EXTERNAL
```

Ответ:
```
OK SERVO_PWR mode=ARDUINO
```

Что это значит:
- **ARDUINO** — “бережный режим” (можно медленнее, аккуратнее)
- **EXTERNAL** — можно полный диапазон и быстрее (если внешнее питание)

---

## 6) Телеметрия (Arduino отдаёт JSON)

Команда:
```
TELEM
```

Ответ:
```
OK TELEM { ...json... }
```

Пример JSON (минимум, что нужно):
```json
{
  "uptime_ms": 123456,
  "fw": {"version":"1.2.0"},
  "motors": { "a": 0, "b": 0 },
  "servos": [
    {"id":1,"deg":90,"attached":true},
    {"id":2,"deg":90,"attached":true}
  ],
  "power": {
    "servo_mode": "ARDUINO",
    "vcc_mv": 4970
  },
  "faults": {
    "estop": false,
    "watchdog": false,
    "brownout": false
  }
}
```

---

## 7) Защиты (обязательно)

### 7.1 Watchdog (Deadman)
Если Arduino **слишком долго не получает команд**, например больше 2 секунд:
- моторы ставим в 0 (стоп)
- сервы ставим в safe pose ИЛИ делаем detach
- в телеметрии `faults.watchdog = true`

### 7.2 E‑STOP (аварийный стоп)
Команда:
```
EStop
```
Ответ:
```
OK ESTOP state=ON
```

После E‑STOP:
- любые команды, которые двигают моторы/сервы, должны отвечать:
```
ERR ESTOP ...
```

Разрешены только команды чтения:
- `PING`, `CAPS`, `TELEM`, `FWVER/VERSION/VER`
и сброс E‑STOP.

Сброс:
```
EStop RESET
```
Ответ:
```
OK ESTOP state=OFF
```

---

## 8) Ошибки и валидация

- неизвестная команда:
```
ERR UNKNOWN_CMD ...
```

- плохие аргументы / диапазон:
```
ERR BAD_ARGS ...
```

- плохой JSON (для SetServos):
```
ERR BAD_JSON ...
```

---

## 9) Чек-лист “сделано”

1) `PING` → `OK PONG`  
2) `CAPS` отдаёт JSON и там `servo_count=5` и **есть `commands`**  
3) `FWVER` (или `VERSION`/`VER`) отдаёт версию прошивки  
4) `SetAEngine 120` / `SetBEngine -50` работают  
5) `SetServo 1 90` работает  
6) `TELEM` отдаёт JSON (motors/servos/faults)  
7) Watchdog останавливает моторы при тишине  
8) E‑STOP блокирует движение до `EStop RESET`  
9) Неверные команды не зависают, а отвечают `ERR ...`
