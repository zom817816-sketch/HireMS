"""Recruitment workflow transitions shared by API and tests."""
from __future__ import annotations


TERMINAL_CANDIDATE_STATUSES = {"淘汰", "Offer已接受", "Offer已拒绝"}
NEXT_ROUND_SUGGESTIONS = {"一面": "二面", "二面": "终面", "终面": "加试"}

ACTION_TRANSITIONS = {
    "待复核": {"pass": "通过", "reject": "淘汰"},
    "通过": {"reject": "淘汰"},
}

OFFER_TRANSITIONS = {
    "Offer待发": {"已发": "Offer已发"},
    "Offer已发": {"已接受": "Offer已接受", "已拒绝": "Offer已拒绝"},
}


class InvalidTransition(ValueError):
    pass


def candidate_action_target(current: str, action: str) -> str:
    target = ACTION_TRANSITIONS.get(current, {}).get(action)
    if not target:
        raise InvalidTransition(f"候选人当前为“{current}”，不能执行该操作")
    return target


def offer_target(current: str, requested: str) -> str:
    target = OFFER_TRANSITIONS.get(current, {}).get(requested)
    if not target:
        raise InvalidTransition(f"候选人当前为“{current}”，不能变更为该 Offer 状态")
    return target


def validate_round_name(round_name: str) -> None:
    value = round_name.strip()
    if not value:
        raise InvalidTransition("请填写面试轮次或环节名称")
    if len(value) > 30:
        raise InvalidTransition("面试轮次或环节名称不能超过 30 个字符")


def expected_next_round(interviews: list[dict]) -> str:
    """Return an editable next-round suggestion, never a mandatory sequence."""
    effective = [item for item in interviews if item.get("status") != "已取消"]
    if not effective:
        return "一面"
    latest = effective[-1]
    if latest.get("status") == "已安排":
        raise InvalidTransition("当前仍有未完成的面试，请先提交评价或取消该面试")
    if latest.get("status") not in {"通过", "下一轮", "继续面试"}:
        raise InvalidTransition("上一轮面试尚未决定继续面试，不能安排下一轮")
    return NEXT_ROUND_SUGGESTIONS.get(latest.get("round_name", ""), "下一轮面试")


def validate_schedule(current: str, interviews: list[dict], round_name: str) -> None:
    validate_round_name(round_name)
    if current not in {"待复核", "通过", "面试中"}:
        raise InvalidTransition(f"候选人当前为“{current}”，不能安排面试")
    # This validates that no interview is pending and that the previous decision
    # was to continue. The returned name is only a UI suggestion.
    expected_next_round(interviews)


def feedback_target(round_name: str, conclusion: str, next_step: str | None = None) -> str:
    validate_round_name(round_name)
    if conclusion == "淘汰":
        return "淘汰"
    if conclusion == "待定":
        return "面试中"
    if conclusion in {"下一轮", "继续面试"}:  # Legacy API compatibility.
        return "面试中"
    if conclusion == "通过":
        if next_step == "继续面试":
            return "面试中"
        if next_step == "Offer":
            return "Offer待发"
        raise InvalidTransition("面试通过后必须指定下一环节为“继续面试”或“Offer”")
    raise InvalidTransition("面试结论必须是通过、淘汰或待定")
