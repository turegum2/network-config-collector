# network-collector

Скрипт параллельного сбора конфигураций с сетевых устройств.  
Поддерживаемые вендоры: **YADRO Kornfeld OS**, Eltex, Cisco, Huawei.

---

## Структура проекта

```
network-collector/
├── main.py              # точка входа
├── collector.py         # логика подключения и сбора
├── kornfeld_driver.py   # кастомный Netmiko-драйвер для Kornfeld OS
├── config.yaml          # настройки (таймауты, потоки, пути)
├── inventory.csv        # список устройств
├── requirements.txt     # зависимости Python
├── commands/
│   ├── kornfeld.txt     # команды для Kornfeld OS (D1156/D2132)
│   ├── eltex.txt        # команды для Eltex MES/ESR
│   ├── cisco.txt        # команды для Cisco IOS/IOS-XE
│   └── huawei.txt       # команды для Huawei VRP
├── output/              # создаётся автоматически — собранные данные
└── logs/                # создаётся автоматически — лог-файлы
```

---

## 1. Установка зависимостей

### Требования

- Python **3.10+**
- SSH-доступ к устройствам (порт 22, read-only учётная запись достаточна)

### Шаг 1 — Проверить версию Python

```bash
python --version   # или python3 --version
```

### Шаг 2 — Создать виртуальное окружение

```bash
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Windows (CMD)
.\venv\Scripts\activate.bat

# Linux / macOS
source venv/bin/activate
```

### Шаг 3 — Установить зависимости

```bash
pip install -r requirements.txt
```

| Пакет | Назначение |
|-------|-----------|
| `netmiko` | SSH-подключение к сетевым устройствам |
| `paramiko` | Базовая SSH-библиотека |
| `PyYAML` | Чтение `config.yaml` |

### Шаг 4 — Проверить установку

```bash
python -c "import netmiko, yaml; print('OK')"
```

---

## 2. Подготовка к запуску

### Инвентарь (inventory.csv)

Hostname в инвентаре **не указывается** — скрипт автоматически читает его
из prompt устройства сразу после подключения (`leaf01#` → hostname = `leaf01`).
Это исключает ошибки ручного ввода и гарантирует совпадение с реальным именем.

```csv
ip,vendor,username,password,port
192.168.1.1,kornfeld,admin,admin,22
192.168.1.2,kornfeld,admin,admin,22
192.168.2.1,eltex,admin,password,22
192.168.3.1,cisco,admin,password,22
192.168.4.1,huawei,admin,Admin@123,22
```

Колонка `port` опциональна (default: 22).

**Поддерживаемые значения поля `vendor`:**

| vendor | Тип устройства |
|--------|---------------|
| `kornfeld` / `kornfeld_dc` | YADRO Kornfeld OS (D1156, D2132) |
| `eltex` | Eltex MES (коммутаторы) |
| `eltex_esr` | Eltex ESR (маршрутизаторы) |
| `cisco` / `cisco_ios` | Cisco IOS |
| `cisco_xe` | Cisco IOS-XE |
| `cisco_nxos` | Cisco NX-OS |
| `huawei` / `huawei_vrp` | Huawei VRP |

---

## 3. Как работает поддержка Kornfeld OS

Kornfeld OS — собственный NOS YADRO, для которого в Netmiko нет встроенного драйвера.
Файл `kornfeld_driver.py` реализует кастомный класс `KornfeldOSDriver`, который:

**1. Правильно обрабатывает prompt:**
Prompt типа `leaf01#` / `kornfeld#` / `spine-01#` совпадает с паттерном `hostname#`.
Драйвер наследуется от `CiscoBaseConnection`, который этот паттерн уже понимает.

**2. Не отправляет Cisco-специфичные команды:**
Kornfeld OS не поддерживает `terminal length 0` и `terminal width 0`.
В `session_preparation()` эти команды исключены.

**3. Автоматически отключает пагинацию:**
Kornfeld OS выводит `--more--` постранично.
Драйвер автоматически добавляет `| no-more` к каждой show-команде
(документировано в User Guide R1.7, раздел «Перенаправление вывода»).
В `commands/kornfeld.txt` писать `| no-more` **не нужно**.

---

## 4. Запуск

### Базовый запуск

```bash
python main.py
```

### Параметры командной строки

```bash
# Указать другой инвентарь
python main.py --inventory my_devices.csv

# Ограничить потоки (при нестабильной сети)
python main.py --workers 5

# Проверить список устройств без подключения
python main.py --dry-run

# Подробный вывод в консоль
python main.py --verbose
```

### Пример вывода

```
Лог-файл: logs/collector_20240315_143022.log
Загружено устройств: 6
Запуск... 6 устройств, 6 параллельных потоков

[  1/6] ✓ leaf01 (192.168.1.1)   ok=69 err=0
[  2/6] ✓ leaf02 (192.168.1.2)   ok=69 err=0
[  3/6] ✓ spine01 (192.168.1.10) ok=69 err=0
[  4/6] ✓ eltex-sw (192.168.2.1) ok=16 err=0
[  5/6] ✗ ? (192.168.3.1)        Таймаут подключения
[  6/6] ✓ huawei-01 (192.168.4.1) ok=16 err=0

======================================================================
  ИТОГО: 6 устройств | ✓ Успешно: 5 | ✗ Ошибок: 1

УСТРОЙСТВА С ОШИБКАМИ:
  ✗ 192.168.3.1  →  Таймаут подключения ...
```

При ошибке подключения hostname не известен и отображается как `?`.

---

## 5. Результаты

Файл на каждое устройство: `output/<hostname>_<ip>_<timestamp>.txt`

```
######################################################################
# Hostname   : leaf01
# IP адрес   : 192.168.1.1
# Вендор     : kornfeld
# Начало     : 2024-03-15 14:30:22
######################################################################

======================================================================
КОМАНДА: show running-configuration
======================================================================
<полный вывод без пагинации>
...
```

Hostname в имени файла и в заголовке — реальный, прочитанный с устройства.

---

## 6. Доработка команд

Файлы в `commands/` — обычный текст, редактируются в любой момент.
Изменения применяются при следующем запуске.

Для Kornfeld: **не добавляйте `| no-more`** вручную — драйвер делает это автоматически.

---

## 7. Частые проблемы

| Проблема | Причина | Решение |
|---|---|---|
| `AuthenticationException` | Неверный логин/пароль | Проверить credentials в inventory.csv |
| `NetmikoTimeoutException` | Устройство недоступно | Увеличить `connect_timeout` в config.yaml |
| `ReadTimeout` на команде | Долгое выполнение | Увеличить `command_timeout` в config.yaml |
| `vendor 'X' не найден` | Новый тип устройства | Добавить в `config.yaml → vendor_map` |
| Kornfeld: `pattern not found` | Нестандартный баннер | Увеличить `banner_timeout` до 30-40 сек |

---

## 8. Производительность (150 устройств)

```yaml
connection:
  connect_timeout: 30
  command_timeout: 120
  inter_command_delay: 0.3

execution:
  max_workers: 10
  retry_count: 2
```

При 10 потоках и ~45 сек на Kornfeld-устройство (69 команд) — 150 устройств ≈ 12-15 минут.
