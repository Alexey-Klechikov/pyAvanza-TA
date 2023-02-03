"""
Logging events.
(File logger + Colored Console logger)
"""

import copy
import datetime
import logging
import os


def displace_message(displacements: tuple, messages: tuple) -> str:
    return " | ".join(
        map(
            lambda y: str(y[0]) + (y[1] - len(str(y[0]))) * " ",
            zip(messages, displacements),
        )
    )


class ColoredFormatter(logging.Formatter):
    """Logging Formatter to add colors and count warning / errors"""

    MAPPING = {
        "DEBUG": 37,  # white
        "INFO": 38,  # grey
        "WARNING": 33,  # yellow
        "ERROR": 31,  # red
        "CRITICAL": 41,
    }  # white on red bg

    PREFIX = "\033["
    SUFFIX = "\033[0m"

    def __init__(self, pattern: str) -> None:
        logging.Formatter.__init__(self, pattern)

        self.messages_counter = 0

    def format(self, record) -> str:
        colored_record = copy.copy(record)
        levelname = colored_record.levelname
        colored_levelname = (
            f"{self.PREFIX}{self.MAPPING.get(levelname, 38)}m{levelname}{self.SUFFIX}"
        )
        colored_record.levelname = colored_levelname

        s = logging.Formatter.format(self, colored_record)
        s = s.replace("main.", "").replace("BULL", "🟢 BULL").replace("BEAR", "🔴 BEAR")

        self.messages_counter += 1

        return s


class OneLineFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, style="%", validate=True):
        super().__init__(fmt, datefmt, style, validate)
        self.displacements = {
            0: {"type": "time", "size": 8},
            1: {"type": "logger", "size": 9},
            2: {"type": "message", "size": 22},
        }

    def format(self, record) -> str:
        s = super(OneLineFormatter, self).format(record)
        s = (
            s.replace("\n", "")
            .replace("main.", "")
            .replace("BULL", "🟢 BULL")
            .replace("BEAR", "🔴 BEAR")
        )

        if s.find("Done"):
            s = s.split("--")[0]

        for i, block in enumerate(s.split("]")[:3]):
            s = s.replace(
                f"{block}]",
                f"{block}]" + (" " * (self.displacements[i]["size"] - len(block))),
            )

            if self.displacements[i]["type"] == "message":
                self.displacements[i]["size"] = max(
                    len(block), self.displacements[i]["size"]
                )

        return s


class Logger:
    def __init__(
        self,
        logger_name: str,
        file_prefix: str,
        log_level: str = "DEBUG",
        file_log_level: str = "INFO",
        console_log_level: str = "INFO",
    ):
        self.log = logging.getLogger(logger_name)
        self.set_handlers(
            True,
            True,
            console_log_level,
            self._get_log_file_name(file_prefix),
            file_log_level,
        )
        self.log.setLevel(os.environ.get("LOGLEVEL", log_level))

    def _get_log_file_name(self, file_prefix: str) -> str:
        log_dir = os.path.join(
            "/".join(os.path.abspath(__file__).split("/")[:-3]), "logs"
        )
        if not os.path.exists(log_dir):
            os.mkdir(log_dir)

        return f"{log_dir}/{file_prefix}_"

    def _create_console_handler(self, console_log_level) -> None:
        ch = logging.StreamHandler()
        ch.setLevel(console_log_level)
        cf = ColoredFormatter("[%(levelname)s] [%(name)s] - %(message)s")
        ch.setFormatter(cf)
        self.log.addHandler(ch)

    def _create_file_handler(self, file_name, log_level, write_mode) -> None:
        fh = logging.FileHandler(file_name, write_mode)
        fh.setLevel(log_level)
        ff = OneLineFormatter(
            "[%(levelname)s] [%(asctime)s] [%(name)s] - %(message)s",
            datefmt="%H:%M:%S",
        )
        fh.setFormatter(ff)
        self.log.addHandler(fh)

    def set_handlers(
        self,
        console_show: bool,
        save_file: bool,
        console_log_level: str,
        log_file_name: str,
        file_log_level: str,
    ) -> None:
        if console_show:
            self._create_console_handler(console_log_level)

        if save_file:
            self._create_file_handler(
                f"{log_file_name}{datetime.datetime.now():%Y-%m-%d}.log",
                file_log_level,
                "a",
            )

        self._create_file_handler(f"{log_file_name}DEBUG.log", "DEBUG", "w")
