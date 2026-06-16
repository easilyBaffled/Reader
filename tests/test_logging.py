import logging

from audibleweb.config import LoggingConfig
from audibleweb.log import _JobIdFilter, _KVFormatter, set_job_id, setup_logging


def _make_handler(log_path):
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.addFilter(_JobIdFilter())
    handler.setFormatter(_KVFormatter())
    return handler


def test_log_line_includes_job_id(tmp_path):
    log_path = tmp_path / "test.log"
    log = logging.getLogger("audibleweb.test_job_id")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    handler = _make_handler(log_path)
    log.addHandler(handler)
    try:
        set_job_id("job-abc-123")
        log.info("processing chunk")
        set_job_id(None)
    finally:
        handler.close()
        log.removeHandler(handler)

    text = log_path.read_text()
    assert "job_id=job-abc-123" in text
    assert "msg=processing chunk" in text


def test_log_line_no_job_id_when_unset(tmp_path):
    log_path = tmp_path / "test.log"
    log = logging.getLogger("audibleweb.test_no_job_id")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    set_job_id(None)
    handler = _make_handler(log_path)
    log.addHandler(handler)
    try:
        log.info("idle poll")
    finally:
        handler.close()
        log.removeHandler(handler)

    text = log_path.read_text()
    assert "job_id=" not in text
    assert "msg=idle poll" in text


def test_setup_logging_creates_file_with_rotation(tmp_path):
    log_path = tmp_path / "sub" / "app.log"
    config = LoggingConfig(
        log_path=str(log_path),
        log_level="DEBUG",
        max_bytes=1024,
        backup_count=2,
    )
    log = logging.getLogger("audibleweb")
    original_handlers = log.handlers[:]
    original_level = log.level
    try:
        handler = setup_logging(config)
        assert handler is not None
        assert log_path.exists()
        set_job_id("job-setup-test")
        logging.getLogger("audibleweb.worker").info("setup test message")
        set_job_id(None)
        text = log_path.read_text()
        assert "job_id=job-setup-test" in text
    finally:
        if handler and handler in log.handlers:
            handler.close()
            log.removeHandler(handler)
        log.handlers = original_handlers
        log.level = original_level


def test_setup_logging_noop_when_no_path():
    config = LoggingConfig(log_path="")
    result = setup_logging(config)
    assert result is None
