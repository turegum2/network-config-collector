"""
collector.py — модуль подключения к устройству и сбора команд.

Hostname не передаётся на вход — он извлекается из prompt'а устройства
сразу после установки SSH-соединения через conn.base_prompt.
Это исключает ошибки ручного ввода и гарантирует соответствие
реальному имени устройства.
"""

import logging
import time
from pathlib import Path
from datetime import datetime

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
    ReadTimeout,
)
from paramiko.ssh_exception import SSHException

from kornfeld_driver import KornfeldOSDriver

logger = logging.getLogger("collector")


# ---------------------------------------------------------------------------
# Загрузка команд из файла вендора
# ---------------------------------------------------------------------------

def load_commands(vendor: str, commands_dir: str | Path) -> list[str]:
    """
    Загружает список команд из файла commands/<vendor>.txt.
    Строки, начинающиеся с '#', и пустые строки пропускаются.
    """
    commands_dir = Path(commands_dir)
    candidates = [vendor, vendor.split("_")[0]]

    for name in candidates:
        path = commands_dir / f"{name}.txt"
        if path.exists():
            commands = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        commands.append(line)
            logger.debug("Загружено %d команд из %s", len(commands), path)
            return commands

    logger.warning(
        "Файл команд для вендора '%s' не найден в %s. Ожидался файл: %s.txt",
        vendor, commands_dir, vendor,
    )
    return []


# ---------------------------------------------------------------------------
# Основной класс сборки данных с устройства
# ---------------------------------------------------------------------------

class DeviceCollector:
    """
    Подключается к одному сетевому устройству, последовательно выполняет
    команды и сохраняет вывод в файл.

    Hostname не передаётся извне — он читается из conn.base_prompt
    сразу после установки соединения.
    """

    def __init__(
        self,
        ip: str,
        vendor: str,
        username: str,
        password: str,
        commands: list[str],
        output_dir: Path,
        cfg: dict,
        port: int = 22,
    ):
        self.ip = ip
        self.vendor = vendor
        self.username = username
        self.password = password
        self.commands = commands
        self.output_dir = output_dir
        self.cfg = cfg
        self.port = port
        # hostname будет заполнен после подключения из base_prompt
        self.hostname: str | None = None
        self.log = logging.getLogger(f"collector.{ip}")

    # ------------------------------------------------------------------
    # Публичный метод — точка входа
    # ------------------------------------------------------------------

    def collect(self) -> dict:
        """
        Выполняет полный цикл сбора данных.

        Returns:
            {
                "ip": str,
                "hostname": str | None,   # заполняется после коннекта из prompt
                "success": bool,
                "output_file": str | None,
                "commands_ok": int,
                "commands_fail": int,
                "error": str | None,
            }
        """
        result = {
            "ip": self.ip,
            "hostname": None,
            "success": False,
            "output_file": None,
            "commands_ok": 0,
            "commands_fail": 0,
            "error": None,
        }

        retry_count = self.cfg["execution"]["retry_count"]
        retry_delay = self.cfg["execution"]["retry_delay"]

        for attempt in range(1, retry_count + 1):
            try:
                self.log.info(
                    "Подключение к %s, попытка %d/%d",
                    self.ip, attempt, retry_count,
                )
                self._run(result)
                result["success"] = True
                break

            except NetmikoAuthenticationException as e:
                msg = f"Ошибка аутентификации: {e}"
                self.log.error(msg)
                result["error"] = msg
                break

            except NetmikoTimeoutException as e:
                msg = f"Таймаут подключения (попытка {attempt}): {e}"
                self.log.warning(msg)
                result["error"] = msg
                if attempt < retry_count:
                    self.log.info("Повтор через %d сек...", retry_delay)
                    time.sleep(retry_delay)

            except SSHException as e:
                msg = f"SSH ошибка (попытка {attempt}): {e}"
                self.log.warning(msg)
                result["error"] = msg
                if attempt < retry_count:
                    time.sleep(retry_delay)

            except Exception as e:
                msg = f"Непредвиденная ошибка (попытка {attempt}): {type(e).__name__}: {e}"
                self.log.error(msg, exc_info=True)
                result["error"] = msg
                if attempt < retry_count:
                    time.sleep(retry_delay)

        return result

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _run(self, result: dict) -> None:
        """Устанавливает соединение, читает hostname из prompt, собирает команды."""
        conn_params = {
            "device_type": self.vendor,
            "host": self.ip,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "conn_timeout": self.cfg["connection"]["connect_timeout"],
            "banner_timeout": self.cfg["connection"]["banner_timeout"],
            "read_timeout_override": self.cfg["connection"]["command_timeout"],
            "fast_cli": False,
        }

        # ConnectHandler валидирует device_type по жёсткому встроенному списку платформ
        # и отклоняет всё неизвестное — в том числе наш 'kornfeld'.
        # Решение: для Kornfeld инстанциируем KornfeldOSDriver напрямую, минуя
        # ConnectHandler. Прямой вызов класса не проверяет device_type.
        # Для всех остальных вендоров ConnectHandler работает как обычно.
        if self.vendor == "kornfeld":
            conn_obj = KornfeldOSDriver(**conn_params)
        else:
            conn_obj = ConnectHandler(**conn_params)

        with conn_obj as conn:
            # Читаем hostname из prompt устройства.
            # base_prompt устанавливается внутри session_preparation() → set_base_prompt().
            # Для leaf01# → base_prompt = "leaf01"
            self.hostname = conn.base_prompt.strip()
            # Убираем "user@" префикс
            if "@" in self.hostname:
                self.hostname = self.hostname.split("@", 1)[1]
            result["hostname"] = self.hostname

            # Переключаем логгер на hostname теперь, когда он известен
            self.log = logging.getLogger(f"collector.{self.hostname}")
            self.log.info(
                "Соединение установлено: hostname=%s ip=%s",
                self.hostname, self.ip,
            )

            output_lines = self._build_header()

            for cmd in self.commands:
                output_lines += self._execute_command(conn, cmd, result)
                time.sleep(self.cfg["connection"]["inter_command_delay"])

            output_lines.append(self._build_footer(result))
            self._save_output(output_lines, result)

    def _execute_command(self, conn, cmd: str, result: dict) -> list[str]:
        """Выполняет одну команду, возвращает строки для записи в файл."""
        separator = "=" * 70
        lines = [
            "",
            separator,
            f"КОМАНДА: {cmd}",
            separator,
        ]

        try:
            self.log.debug("[%s] Выполняю: %s", self.ip, cmd)
            output = conn.send_command(
                cmd,
                read_timeout=self.cfg["connection"]["command_timeout"],
            )
            lines.append(output)
            result["commands_ok"] += 1
            self.log.debug("[%s] OK: %s", self.ip, cmd)

        except ReadTimeout:
            error_msg = (
                f"[TIMEOUT] Команда превысила таймаут "
                f"{self.cfg['connection']['command_timeout']}s: '{cmd}'"
            )
            lines.append(error_msg)
            result["commands_fail"] += 1
            self.log.warning("[%s] Таймаут команды: %s", self.ip, cmd)

        except Exception as e:
            error_msg = f"[ERROR] {type(e).__name__}: {e}"
            lines.append(error_msg)
            result["commands_fail"] += 1
            self.log.error("[%s] Ошибка команды '%s': %s", self.ip, cmd, e)

        return lines

    def _build_header(self) -> list[str]:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [
            "#" * 70,
            f"# Hostname   : {self.hostname}",
            f"# IP адрес   : {self.ip}",
            f"# Вендор     : {self.vendor}",
            f"# Начало     : {ts}",
            "#" * 70,
        ]

    def _build_footer(self, result: dict) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"\n{'#' * 70}\n"
            f"# Завершено  : {ts}\n"
            f"# Успешно    : {result['commands_ok']} команд\n"
            f"# Ошибок     : {result['commands_fail']} команд\n"
            f"{'#' * 70}\n"
        )

    def _save_output(self, lines: list[str], result: dict) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Имя файла: <hostname>_<ip>_<timestamp>.txt
        filename = f"{self.hostname}_{self.ip}_{ts}.txt"
        filepath = self.output_dir / filename

        with open(filepath, "w", encoding=self.cfg["output"]["encoding"]) as f:
            f.write("\n".join(lines))

        result["output_file"] = str(filepath)
        self.log.info(
            "Данные сохранены: %s (%d команд, %d ошибок)",
            filepath, result["commands_ok"], result["commands_fail"],
        )
