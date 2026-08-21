"""Feishu adapters for interactive recruiting cards and interview calendars."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import httpx

from config.config import settings


class FeishuWorkflowClient:
    base_url = "https://open.feishu.cn/open-apis"

    @staticmethod
    def configured_for_messages() -> bool:
        return bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET and settings.FEISHU_HR_RECEIVER_IDS)

    @staticmethod
    def configured_for_calendar() -> bool:
        return bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET and settings.FEISHU_CALENDAR_ID)

    def _token(self) -> str:
        response = httpx.post(f"{self.base_url}/auth/v3/tenant_access_token/internal", json={
            "app_id": settings.FEISHU_APP_ID, "app_secret": settings.FEISHU_APP_SECRET,
        }, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(data.get("msg", "获取飞书访问凭证失败"))
        return data["tenant_access_token"]

    def _post(self, path: str, payload: dict, params: dict | None = None) -> dict:
        response = httpx.post(f"{self.base_url}{path}", params=params, json=payload, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=utf-8",
        }, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(data.get("msg", "飞书请求失败"))
        return data.get("data", {})

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = httpx.get(f"{self.base_url}{path}", params=params, headers={
            "Authorization": f"Bearer {self._token()}",
        }, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(data.get("msg", "飞书忙闲查询失败"))
        return data.get("data", {})

    def _patch(self, path: str, payload: dict, params: dict | None = None) -> dict:
        response = httpx.patch(f"{self.base_url}{path}", params=params, json=payload, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=utf-8",
        }, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(data.get("msg", "飞书日历更新失败"))
        return data.get("data", {})

    def _delete(self, path: str, params: dict | None = None) -> None:
        response = httpx.delete(f"{self.base_url}{path}", params=params, headers={
            "Authorization": f"Bearer {self._token()}",
        }, timeout=30)
        response.raise_for_status()
        if response.content:
            data = response.json()
            if data.get("code", 0) != 0:
                raise RuntimeError(data.get("msg", "飞书日历删除失败"))

    def busy_interviewers(self, start: datetime, end: datetime, interviewer_ids: list[str]) -> list[str]:
        """Query calendars explicitly mapped to interviewers in environment config.

        A tenant app cannot infer employees' primary calendars without delegated
        access. The explicit map keeps that authorization scope visible.
        """
        try:
            calendar_map = json.loads(settings.FEISHU_INTERVIEWER_CALENDAR_MAP or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("FEISHU_INTERVIEWER_CALENDAR_MAP 必须是 JSON 对象") from exc
        busy = []
        for interviewer_id in interviewer_ids:
            calendar_id = calendar_map.get(interviewer_id)
            if not calendar_id:
                continue
            data = self._get(f"/calendar/v4/calendars/{calendar_id}/events", {
                "start_time": str(int(start.timestamp())), "end_time": str(int(end.timestamp())),
            })
            if data.get("items") or data.get("events"):
                busy.append(interviewer_id)
        return busy

    @staticmethod
    def _candidate_card(candidate: dict) -> dict:
        score = round(float(candidate.get("overall_score", 0)) * 100)
        candidate_id = candidate["id"]
        detail_url = f"{settings.FEISHU_PUBLIC_BASE_URL.rstrip('/')}/ui/#candidate-{candidate_id}" if settings.FEISHU_PUBLIC_BASE_URL else ""
        actions = [
            {"tag": "button", "text": {"tag": "plain_text", "content": "通过"}, "type": "primary", "value": {"candidate_id": candidate_id, "action": "pass"}},
            {"tag": "button", "text": {"tag": "plain_text", "content": "淘汰"}, "type": "danger", "value": {"candidate_id": candidate_id, "action": "reject"}},
            {"tag": "button", "text": {"tag": "plain_text", "content": "安排面试"}, "type": "default", "value": {"candidate_id": candidate_id, "action": "schedule"}},
        ]
        if detail_url:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"}, "type": "default", "url": detail_url})
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"候选人推荐 · {candidate.get('job_name', '未命名岗位')}"}, "template": "green"},
            "elements": [
                {"tag": "div", "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**候选人**\n{candidate.get('name') or '未识别'}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**匹配度**\n{score}/100"}},
                    {"is_short": False, "text": {"tag": "lark_md", "content": f"**技能**\n{', '.join(candidate.get('skills') or []) or '待提取'}"}},
                ]},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": (candidate.get("analysis") or "已完成 AI 初筛")[:300]}]},
                {"tag": "action", "actions": actions},
            ],
        }

    def send_candidate_card(self, candidate: dict, receiver_ids: list[str] | None = None) -> int:
        if not self.configured_for_messages():
            raise ValueError("飞书消息未配置，请填写 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_HR_RECEIVER_IDS")
        recipients = receiver_ids or [x.strip() for x in settings.FEISHU_HR_RECEIVER_IDS.split(",") if x.strip()]
        card = json.dumps(self._candidate_card(candidate), ensure_ascii=False)
        for receiver in recipients:
            self._post("/im/v1/messages", {"receive_id": receiver, "msg_type": "interactive", "content": card}, {"receive_id_type": "open_id"})
        return len(recipients)

    def send_text(self, content: str, receiver_ids: list[str] | None = None) -> int:
        if not self.configured_for_messages():
            raise ValueError("飞书消息未配置")
        recipients = receiver_ids or [x.strip() for x in settings.FEISHU_HR_RECEIVER_IDS.split(",") if x.strip()]
        for receiver in recipients:
            self._post("/im/v1/messages", {"receive_id": receiver, "msg_type": "text", "content": json.dumps({"text": content}, ensure_ascii=False)}, {"receive_id_type": "open_id"})
        return len(recipients)

    @staticmethod
    def _interview_event_payload(candidate: dict, interview: dict) -> dict:
        start = datetime.fromisoformat(interview["start_at"])
        end = datetime.fromisoformat(interview["end_at"])
        return {
            "summary": f"{interview['round_name']}｜{candidate.get('name') or '候选人'}",
            "description": f"岗位：{candidate.get('job_name', '')}\n候选人邮箱：{candidate.get('email') or ''}\n备注：{interview.get('note') or ''}",
            "start_time": {"timestamp": str(int(start.timestamp())), "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": str(int(end.timestamp())), "timezone": "Asia/Shanghai"},
            "free_busy_status": "busy",
            "location": {"name": interview.get("location") or "线上"},
            "reminders": [{"minutes": 60}],
            "attendee_ability": "can_modify_event",
        }

    def create_interview_event(self, candidate: dict, interview: dict) -> str:
        if not self.configured_for_calendar():
            raise ValueError("飞书日历未配置，请填写 FEISHU_CALENDAR_ID")
        payload = self._interview_event_payload(candidate, interview)
        data = self._post(
            f"/calendar/v4/calendars/{settings.FEISHU_CALENDAR_ID}/events", payload,
            {"idempotency_key": str(uuid.uuid4()), "user_id_type": "open_id"},
        )
        event = data.get("event", data)
        return event.get("event_id", "")

    def update_interview_event(self, event_id: str, candidate: dict, interview: dict) -> None:
        if not self.configured_for_calendar() or not event_id:
            raise ValueError("飞书日历事件尚未创建")
        self._patch(
            f"/calendar/v4/calendars/{settings.FEISHU_CALENDAR_ID}/events/{event_id}",
            self._interview_event_payload(candidate, interview),
            {"user_id_type": "open_id"},
        )

    def cancel_interview_event(self, event_id: str) -> None:
        if not self.configured_for_calendar() or not event_id:
            raise ValueError("飞书日历事件尚未创建")
        self._delete(
            f"/calendar/v4/calendars/{settings.FEISHU_CALENDAR_ID}/events/{event_id}",
            {"user_id_type": "open_id"},
        )
