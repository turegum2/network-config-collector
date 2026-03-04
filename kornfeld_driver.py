"""
kornfeld_driver.py — кастомный Netmiko-драйвер для Kornfeld OS.

Почему нужен кастомный драйвер:
  - Kornfeld OS — собственный NOS YADRO, не SONiC и не IOS
  - В Netmiko нет встроенного типа 'kornfeld'
  - Prompt: leaf01# (любое hostname + #) — совпадает с CiscoIOS-паттерном,
    но Kornfeld не понимает 'terminal length 0' и 'terminal width 0'
  - Пагинация отключается через '| no-more' (документировано в UG 1.7)

Архитектура:
  - Наследуемся от CiscoBaseConnection (готовый prompt-паттерн '#')
  - Переопределяем session_preparation(): убираем Cisco-специфичные команды
  - Переопределяем send_command(): автоматически добавляем '| no-more'
    к show-командам, чтобы гарантированно получить весь вывод
"""

import re
import logging
from netmiko.cisco_base_connection import CiscoBaseConnection

logger = logging.getLogger(__name__)

# Команды, к которым НЕЛЬЗЯ добавлять '| no-more'
# (не show-команды или те, что выдают structured output)
_NO_MORE_BLACKLIST = re.compile(
    r"^(ping|traceroute|traceroute6|ping6|copy|write|reboot|poweroff|configure|exit|end|no )",
    re.IGNORECASE,
)

# show-команды, вывод которых короткий и пагинация не нужна (оптимизация)
_SHORT_OUTPUT_CMDS = re.compile(
    r"^show (clock|uptime|version|users|ztp|reboot-cause|core config|core list)",
    re.IGNORECASE,
)


def _append_no_more(command: str) -> str:
    """
    Добавляет '| no-more' к show-командам для отключения пагинации.
    Kornfeld OS документирует этот субкоманд в UG 1.7 (раздел 'no-more').
    """
    cmd = command.strip()
    if not cmd.lower().startswith("show"):
        return cmd
    if _NO_MORE_BLACKLIST.match(cmd):
        return cmd
    if "| no-more" in cmd or "|no-more" in cmd:
        return cmd  # уже есть
    return f"{cmd} | no-more"


class KornfeldOSDriver(CiscoBaseConnection):
    """
    Netmiko-драйвер для YADRO Kornfeld OS (DC-коммутаторы D1156/D2132).

    Prompt-паттерн: hostname# (например, leaf01#, kornfeld#, spine-01#)
    Пагинация: отключается через '| no-more' на каждой команде.
    """

    # Netmiko использует это поле для регистрации типа устройства
    device_type = "kornfeld"

    def session_preparation(self) -> None:
        """
        Подготовка сессии после логина.

        Kornfeld OS НЕ поддерживает:
          - terminal length 0  (IOS-специфично)
          - terminal width 0   (IOS-специфично)
          - terminal pager 0   (EOS-специфично)

        Вместо этого пагинация управляется через '| no-more' в каждой команде.
        """
        # Ждём появления prompt после баннера/приветствия
        self._test_channel_read(pattern=r"#")
        # Определяем базовый prompt (hostname#)
        self.set_base_prompt()
        # Никаких дополнительных команд не нужно

    def send_command(self, command_string: str, **kwargs):
        """
        Переопределяем send_command: автоматически добавляем '| no-more'
        ко всем show-командам перед отправкой.
        """
        modified_cmd = _append_no_more(command_string)
        if modified_cmd != command_string:
            logger.debug("Kornfeld: добавлен | no-more → '%s'", modified_cmd)
        return super().send_command(modified_cmd, **kwargs)

    def check_enable_mode(self, check_string: str = "#") -> bool:
        """Kornfeld всегда входит сразу в privileged mode (#)."""
        return True

    def enable(self, cmd: str = "", pattern: str = "", **kwargs) -> str:
        """Enable не нужен — уже в privileged mode."""
        return ""

    def exit_enable_mode(self, exit_command: str = "") -> str:
        """Нет необходимости выходить из enable."""
        return ""

    def config_mode(self, config_command: str = "configure terminal", **kwargs) -> str:
        """Переход в режим конфигурирования."""
        return super().config_mode(config_command=config_command, **kwargs)

    def exit_config_mode(self, exit_config: str = "exit", **kwargs) -> str:
        """Выход из режима конфигурирования."""
        return super().exit_config_mode(exit_config=exit_config, **kwargs)

