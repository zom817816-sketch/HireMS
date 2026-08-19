import httpx
import pytest

from app.services.feishu_bitable import FeishuBitableWriter


def test_bitable_writer_explains_write_permission_error():
    response = httpx.Response(403, json={"code": 1254302, "msg": "Permission denied"})

    with pytest.raises(PermissionError, match="添加文档应用"):
        FeishuBitableWriter._raise_bitable_write_error(response)


def test_bitable_writer_accepts_success_response():
    response = httpx.Response(200, json={"code": 0, "msg": "success"})

    FeishuBitableWriter._raise_bitable_write_error(response)


def test_bitable_writer_omits_blank_phone_number(monkeypatch):
    writer = FeishuBitableWriter()
    captured = {}

    monkeypatch.setattr(writer, "configured", lambda: True)
    monkeypatch.setattr(writer, "_token", lambda: "test-token")
    monkeypatch.setattr("app.services.feishu_bitable.settings.FEISHU_EXPORT_MIN_SCORE", 0.7)

    def fake_post(*_, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    monkeypatch.setattr("app.services.feishu_bitable.httpx.post", fake_post)
    writer.write_candidates([{"name": "候选人", "overall_score": 0.9, "phone": ""}], "测试岗位")

    assert "电话" not in captured["records"][0]["fields"]
