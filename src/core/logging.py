from loguru import logger
import inspect
from functools import wraps
import sys


class CustomLogger:
    def __init__(self):
        # Формат с поддержкой extra полей (например, real_ip)
        log_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[real_ip]}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )

        # Формат без extra полей для обратной совместимости
        simple_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )

        # Убираем стандартный обработчик
        logger.remove()

        # Патчер для установки дефолтных значений extra полей
        def patcher(record):
            record["extra"].setdefault("real_ip", "N/A")
            record["extra"].setdefault("path", "N/A")
            return record

        # Конфигурируем логгер с патчером
        logger.configure(patcher=patcher)

        # Добавляем обработчик для консоли
        logger.add(sys.stderr, format=simple_format, level="INFO", colorize=True)

        # Добавляем файловые обработчики
        logger.add(
            "logs/logfile.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[real_ip]:15} | {name}:{function}:{line} | {message}",
            level="DEBUG",
            rotation="100 MB",
            retention="30 days",
            compression="zip",
        )

        logger.add(
            "logs/errorfile.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[real_ip]:15} | {name}:{function}:{line} | {message}",
            level="ERROR",
            rotation="50 MB",
            retention="60 days",
            compression="zip",
        )

    def _get_caller_info(self):
        frame = inspect.stack()[2]
        module = inspect.getmodule(frame[0])
        filename = module.__file__ if module else "unknown"
        return filename

    def info(self, message):
        filename = self._get_caller_info()
        logger.info(f"{filename} - INFO - {message}")

    def debug(self, message):
        filename = self._get_caller_info()
        logger.debug(f"{filename} - DEBUG - {message}")

    def error(self, message):
        filename = self._get_caller_info()
        logger.error(f"{filename} - ERROR - {message}")

    def warning(self, message):
        filename = self._get_caller_info()
        logger.warning(f"{filename} - WARNING - {message}")

    def critical(self, message):
        filename = self._get_caller_info()
        logger.critical(f"{filename} - CRITICAL - {message}")

    def log_exceptions(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                self.error(f"Exception in {func.__name__}: {e}")
                raise

        return wrapper


main_logger = CustomLogger()
