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
