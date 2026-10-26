from __future__ import annotations

import logging
from typing import Iterable

from runtime.settings import mask_value


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = sorted({secret for secret in secrets if secret}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, mask_value("SECRET", secret))
        record.msg = message
        record.args = ()
        return True


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    root = logging.getLogger()
    redactor = SecretRedactionFilter(secrets)
    root.addFilter(redactor)
    # Logger filters do not run for records propagated from child loggers;
    # handler filters do. Protect every already-installed output as well.
    for handler in root.handlers:
        handler.addFilter(redactor)
