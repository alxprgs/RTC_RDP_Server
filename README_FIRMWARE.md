# Arduino Motor Bridge — Firmware Spec (v2)

Этот документ — **техническое ТЗ** для новой прошивки Arduino под проект *Arduino Motor Bridge*.

Сервер (FastAPI) общается с Arduino по USB‑Serial и ожидает:
- управление моторами (A/B)
- управление **до 5 сервоприводов**
- режим питания серв: **ARDUINO / EXTERNAL**
- телеметрия в JSON
- защиты: **watchdog**, **E‑STOP**, строгая валидация
- **capabilities + supported commands** (через `CAPS`)
- **версия прошивки** (FWVER/VERSION/VER)

> Сервер может **строго блокировать** неподдерживаемые команды (HTTP 501), если прошивка вернула список `commands` в CAPS.

---

## 1) Транспорт и формат

### Serial
- Скорость: задаётся на сервере (обычно 115200)
- Команда = одна строка ASCII, заканчивается `\n`
- Ответ = одна строка, заканчивается `\n`

### Ответы
- Успех: `OK <TOKEN> ...`
- Ошибка: `ERR <CODE> <MESSAGE>`

**Требование:** прошивка должна отвечать на любую команду либо `OK ...`, либо `ERR ...` (без молчания).

---

## 2) Команды “идентификация устройства”

### 2.1 Health-check
**Команда**
```
PING
```
**Ответ**
```
OK PONG
```

### 2.2 CAPS (возможности + список команд)
**Команда**
```
CAPS
```

**Ответ**
```
OK CAPS { ...json... }
```

#### Рекомендуемая JSON-схема CAPS
```json
{
  "device":"rov",
  "fw":{"version":"1.2.0","git":"abc123"},
  "servo_count":5,
  "servo_deg_min":0,
  "servo_deg_max":180,
  "supports_batch":true,
  "supports_detach":true,
  "supports_estop":true,
  "supports_fwver":true,
  "commands":[
    "PING","CAPS","TELEM","FWVER",
    "SetAEngine","SetBEngine","SetAllEngine",
    "SetServo","SetServos","ServoCenter",
    "ServoAttach","ServoDetach","ServoDetachAll",
    "ServoPwr","EStop"
  ]
}
```

**`commands`** — список реально поддерживаемых команд. Сервер использует его для:
- совместимости,
- отображения возможностей,
- блокировки неподдерживаемого (HTTP 501).

### 2.3 Версия прошивки (Firmware Version)
Прошивка должна поддерживать **хотя бы одну** из команд: `FWVER`, `VERSION`, `VER`.

**Команда**
```
FWVER
```
**Ответ (варианты)**
```
OK FWVER 1.2.0
```
или
```
OK FWVER {"version":"1.2.0","git":"abc123"}
```

---

## 3) Моторы

### 3.1 Установка тяги (PWM)
**Команды**
```
SetAEngine <speed>
SetBEngine <speed>
SetAllEngine <speed>
```
- `speed`: `-255..255`

**Ответы**
```
OK SETAENGINE
OK SETBENGINE
OK SETALLENGINE
```

**Ошибки**
- `ERR BAD_ARGS ...`
- `ERR ESTOP ...`

---

## 4) Сервоприводы (до 5)

### 4.1 Установка одного серво
```
SetServo <id> <deg>
```
- `id`: `1..5`
- `deg`: `0..180`

Ответ (пример):
```
OK SETSERVO id=3 deg=120
```

### 4.2 Batch (желательно)
```
SetServos {"items":[{"id":1,"deg":90},{"id":2,"deg":20}]}
```
Ответ:
```
OK SETSERVOS {"applied":2}
```

### 4.3 Safe pose
```
ServoCenter
```
Ответ:
```
OK SERVO_CENTER
```

### 4.4 Attach/Detach (опционально)
```
ServoAttach <id>
ServoDetach <id>
ServoDetachAll
```

---

## 5) Режим питания серв
```
ServoPwr ARDUINO
ServoPwr EXTERNAL
```
Ответ:
```
OK SERVO_PWR mode=ARDUINO
```

---

## 6) Телеметрия (JSON)
```
TELEM
```
Ответ:
```
OK TELEM { ...json... }
```

Минимальный JSON:
```json
{
  "uptime_ms": 123456,
  "fw": {"version":"1.2.0"},
  "motors": { "a": 0, "b": 0 },
  "servos": [{"id":1,"deg":90,"attached":true}],
  "faults": {"estop": false, "watchdog": false, "brownout": false}
}
```

---

## 7) Защиты (обязательно)

### 7.1 Watchdog
Если нет валидных команд дольше `T` секунд:
- моторы → 0
- сервы → safe pose или detach
- `faults.watchdog = true`

### 7.2 E‑STOP
```
EStop
EStop RESET
```
После `EStop` любые команды движения → `ERR ESTOP ...`.

---

## 8) Acceptance Criteria

1. `PING` → `OK PONG`
2. `CAPS` → `OK CAPS {json}` (есть `servo_count` и `commands`)
3. `FWVER/VERSION/VER` возвращает версию
4. `TELEM` возвращает JSON
5. Watchdog останавливает моторы при тишине
6. E‑STOP блокирует движение до RESET
