import io
import logging

from operations.logging_utils import configure_logging


def test_child_logger_redacts_handler_output() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    root.handlers.clear()
    stream = io.StringIO()
    root.addHandler(logging.StreamHandler(stream))
    try:
        configure_logging(secrets=["synthetic-review-secret"])
        logging.getLogger("vibetrading.child").warning("authorization=%s", "synthetic-review-secret")
        assert "synthetic-review-secret" not in stream.getvalue()
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
