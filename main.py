"""
main.py — точка входа. Запускает параллельный сбор конфигураций
с сетевых устройств.

Использование:
    python main.py
    python main.py --inventory devices.csv --config config.yaml --workers 5
    python main.py --dry-run   # показать список устройств без подключения
"""

import argparse
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

from collector import DeviceCollector, load_commands


# ---------------------------------------------------------------------------
# Настройка логирования
# ---------------------------------------------------------------------------

def setup_logging(log_dir: Path, verbose: bool = False):
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"collector_{ts}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    fmt_file = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt_file)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))

    root_logger.addHandler(fh)
    root_logger.addHandler(ch)

    progress_logger = logging.getLogger("progress")
    ph = logging.StreamHandler(sys.stdout)
    ph.setLevel(logging.INFO)
    ph.setFormatter(logging.Formatter("%(message)s"))
    progress_logger.addHandler(ph)
    progress_logger.propagate = False

    print(f"Лог-файл: {log_file}", flush=True)
    return root_logger, log_file


# ---------------------------------------------------------------------------
# Загрузка конфигурации и инвентаря
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_inventory(path: str) -> list[dict]:
    """
    Загружает инвентарь из CSV.
    Обязательные колонки: ip, vendor, username, password
    Опциональные: port (default: 22)
    Hostname не указывается — будет получен с устройства после подключения.
    """
    devices = []
    required = {"ip", "vendor", "username", "password"}

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            print(f"[ОШИБКА] В CSV отсутствуют колонки: {missing}", file=sys.stderr)
            sys.exit(1)

        for i, row in enumerate(reader, 1):
            if not row["ip"].strip():
                logging.getLogger("main").warning(
                    "Строка %d: пустой IP, пропускаем", i
                )
                continue
            devices.append({
                "ip": row["ip"].strip(),
                "vendor": row["vendor"].strip().lower(),
                "username": row["username"].strip(),
                "password": row["password"].strip(),
                "port": int(row.get("port", 22) or 22),
            })

    return devices


# ---------------------------------------------------------------------------
# Подготовка задачи для одного устройства
# ---------------------------------------------------------------------------

def build_task(device: dict, cfg: dict, commands_dir: Path, output_dir: Path):
    vendor_map = cfg.get("vendor_map", {})
    netmiko_type = vendor_map.get(device["vendor"])

    if not netmiko_type:
        logging.getLogger("main").error(
            "Вендор '%s' не найден в vendor_map (%s). "
            "Добавьте его в config.yaml → vendor_map.",
            device["vendor"], device["ip"],
        )
        return None

    commands = load_commands(device["vendor"], commands_dir)
    if not commands:
        logging.getLogger("main").warning(
            "Нет команд для вендора '%s', устройство %s будет пропущено.",
            device["vendor"], device["ip"],
        )
        return None

    return DeviceCollector(
        ip=device["ip"],
        vendor=netmiko_type,
        username=device["username"],
        password=device["password"],
        commands=commands,
        output_dir=output_dir,
        cfg=cfg,
        port=device["port"],
    )


# ---------------------------------------------------------------------------
# Вывод итоговой таблицы результатов
# ---------------------------------------------------------------------------

def print_summary(results: list[dict], log_file: Path) -> None:
    total = len(results)
    ok = sum(1 for r in results if r["success"])
    fail = total - ok

    print("\n" + "=" * 70)
    print(f"  ИТОГО: {total} устройств | OK: {ok} | Ошибок: {fail}")
    print("=" * 70)

    if fail:
        print("\nУСТРОЙСТВА С ОШИБКАМИ:")
        for r in results:
            if not r["success"]:
                print(f"  [!!] {r['ip']}  →  {r['error']}")

    print(f"\nПолный лог: {log_file}")
    print(f"Собранные данные: output/\n")


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Сбор конфигураций с сетевых устройств (Kornfeld/Eltex/Cisco/Huawei)"
    )
    parser.add_argument(
        "--inventory", default="inventory.csv",
        help="Путь к CSV-файлу инвентаря (default: inventory.csv)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Путь к файлу конфигурации (default: config.yaml)"
    )
    parser.add_argument(
        "--commands-dir", default="commands",
        help="Папка с файлами команд вендоров (default: commands/)"
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Количество параллельных потоков (переопределяет config.yaml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать список устройств без подключения"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Подробный вывод в консоль (DEBUG уровень)"
    )
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"[ОШИБКА] Файл конфигурации не найден: {args.config}", file=sys.stderr)
        sys.exit(1)
    cfg = load_config(args.config)

    log_dir = Path(cfg["output"]["log_dir"])
    root_logger, log_file = setup_logging(log_dir, verbose=args.verbose)
    progress = logging.getLogger("progress")

    if not Path(args.inventory).exists():
        print(f"[ОШИБКА] Файл инвентаря не найден: {args.inventory}", file=sys.stderr)
        sys.exit(1)
    devices = load_inventory(args.inventory)
    progress.info(f"Загружено устройств: {len(devices)}")

    output_dir = Path(cfg["output"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    commands_dir = Path(args.commands_dir)

    tasks = []
    for device in devices:
        task = build_task(device, cfg, commands_dir, output_dir)
        if task:
            tasks.append(task)

    if not tasks:
        print("[ОШИБКА] Нет корректных задач для выполнения.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"\n{'=' * 70}")
        print(f"DRY-RUN: будет обработано {len(tasks)} устройств")
        print(f"{'=' * 70}")
        for task in tasks:
            print(
                f"\n  {task.ip}"
                f"  vendor={task.vendor}"
                f"  команд={len(task.commands)}"
            )
        print()
        sys.exit(0)

    max_workers = args.workers or cfg["execution"]["max_workers"]
    max_workers = min(max_workers, len(tasks))
    progress.info(f"Запуск сбора: {len(tasks)} устройств, {max_workers} потоков")
    print(f"\nЗапуск... {len(tasks)} устройств, {max_workers} параллельных потоков\n", flush=True)

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {executor.submit(task.collect): task for task in tasks}

        for future in as_completed(future_to_task):
            completed += 1
            task = future_to_task[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "ip": task.ip,
                    "hostname": None,
                    "success": False,
                    "output_file": None,
                    "commands_ok": 0,
                    "commands_fail": 0,
                    "error": f"Критическая ошибка потока: {e}",
                }
                logging.getLogger("main").critical(
                    "Критическая ошибка для %s: %s", task.ip, e, exc_info=True
                )

            results.append(result)

            status = "[OK]" if result["success"] else "[!!]"
            # hostname известен только при успехе; при ошибке подключения — None
            hostname_str = result["hostname"] or "?"
            detail = (
                f"ok={result['commands_ok']} err={result['commands_fail']}"
                if result["success"]
                else result["error"] or "неизвестная ошибка"
            )
            print(
                f"[{completed:>3}/{len(tasks)}] {status} {hostname_str} ({result['ip']})  {detail}",
                flush=True,
            )

    print_summary(results, log_file)


if __name__ == "__main__":
    main()
