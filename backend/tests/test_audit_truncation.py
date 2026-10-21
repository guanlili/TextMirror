"""审计日志只保存输入预览，全文由校对记录承载。"""
from starlette.requests import Request

from app.services import audit_log as audit_log_module
from app.services.audit_log import record_audit_log as original_record_audit_log


def _request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"test")],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        "scheme": "http", "query_string": b"",
    })


def test_audit_input_is_truncated_but_keeps_original_length(monkeypatch):
    captured = {}
    monkeypatch.setattr(audit_log_module, "_spawn_audit_task", lambda data: captured.update(data))

    original_record_audit_log(
        _request(), "proofread_text", input_text="字" * 1200,
        extra_params={"domain": "general"},
    )

    assert len(captured["input_text"]) == 500
    assert captured["input_length"] == 1200
    assert captured["extra_params"]["domain"] == "general"
    assert captured["extra_params"]["input_truncated"] is True
    assert captured["extra_params"]["input_original_length"] == 1200


def test_short_audit_input_is_unchanged(monkeypatch):
    captured = {}
    monkeypatch.setattr(audit_log_module, "_spawn_audit_task", lambda data: captured.update(data))

    original_record_audit_log(_request(), "proofread_text", input_text="短文本")

    assert captured["input_text"] == "短文本"
    assert captured["input_length"] == 3
    assert captured["extra_params"] is None
