"""Minimal, auditable Bitable writer using Feishu's tenant access token flow."""
from __future__ import annotations

from typing import Any

import httpx

from config.config import settings


class FeishuBitableWriter:
    base_url = "https://open.feishu.cn/open-apis"

    @staticmethod
    def configured() -> bool:
        return bool(
            settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET
            and settings.FEISHU_BITABLE_APP_TOKEN and settings.FEISHU_BITABLE_TABLE_ID
        )

    def _token(self) -> str:
        response = httpx.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.FEISHU_APP_ID, "app_secret": settings.FEISHU_APP_SECRET},
            timeout=15,
        )
        data = response.json()
        if response.status_code == 403:
            raise PermissionError("飞书拒绝应用访问。请检查自建应用的凭证与可用范围。")
        response.raise_for_status()
        if data.get("code", 0) != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(data.get("msg", "获取飞书 tenant_access_token 失败"))
        return data["tenant_access_token"]

    @staticmethod
    def _raise_bitable_write_error(response: httpx.Response) -> None:
        """Raise an actionable error while keeping Feishu response details out of logs/UI."""
        try:
            data = response.json()
        except ValueError:
            data = {}
        code = data.get("code")
        message = str(data.get("msg") or "")
        if response.status_code == 403 or code == 1254302 or "permission" in message.lower():
            raise PermissionError(
                "飞书拒绝写入多维表格（403）。请在目标多维表格中通过“…”→“添加文档应用”"
                "添加此自建应用，并授予可编辑或可管理权限；同时确认应用已开通多维表格读写权限。"
            )
        if response.is_error or code not in (None, 0):
            detail = message or f"HTTP {response.status_code}"
            raise RuntimeError(f"飞书多维表格写入失败：{detail}")

    def write_candidates(self, candidates: list[dict[str, Any]], job_name: str) -> int:
        if not self.configured():
            raise ValueError("多维表格尚未配置。请在 .env 填写 FEISHU_APP_ID、APP_SECRET、APP_TOKEN、TABLE_ID。")
        records = []
        for candidate in candidates:
            if float(candidate.get("overall_score", 0)) < settings.FEISHU_EXPORT_MIN_SCORE:
                continue
            records.append({"fields": {
                "姓名": candidate.get("name") or "未知",
                "邮箱": candidate.get("email") or "",
                "电话": candidate.get("phone") or "",
                "岗位": job_name,
                "匹配度": round(float(candidate.get("overall_score", 0)) * 100),
                "技能": ", ".join(candidate.get("skills") or []),
                "期望地点": ", ".join(candidate.get("preferred_locations") or []),
                "AI分析": candidate.get("analysis") or "",
                "处理状态": "待复核",
            }})
        if not records:
            return 0
        token = self._token()
        url = (f"{self.base_url}/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}"
               f"/tables/{settings.FEISHU_BITABLE_TABLE_ID}/records/batch_create")
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"records": records},
            timeout=30,
        )
        self._raise_bitable_write_error(response)
        return len(records)
