"""JSON-lines logging. Log IDs, counts and outcomes — never names, raw rows or connection strings (D-46).

Usage: log.info("import_completed", extra={"fields": {"import_id": ..., "accepted": 6}})
"""
import json
import logging

log = logging.getLogger("opsib")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "msg": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log.handlers[:] = [handler]
    log.setLevel(level)
    log.propagate = False
