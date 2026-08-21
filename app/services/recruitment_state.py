"""Strict recruitment workflow transitions shared by API and tests."""
from __future__ import annotations


TERMINAL_CANDIDATE_STATUSES = {"淘汰", "Offer已接受", "Offer已拒绝"}
ROUND_ORDER = {"一面": 1, "二面": 2, "终面": 3}
NEXT_ROUND = {"一面": "二面", "二面": "终面"}

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
    if round_name not in ROUND_ORDER:
        raise InvalidTransition("面试轮次只能是一面、二面或终面")


def expected_next_round(interviews: list[dict]) -> str:
    """Return the next schedulable round, ignoring cancelled interviews."""
    effective = [item for item in interviews if item.get("status") != "已取消"]
    if not effective:
        return "一面"
    latest = max(effective, key=lambda item: ROUND_ORDER.get(item.get("round_name", ""), 0))
    if latest.get("status") == "已安排":
        raise InvalidTransition("当前仍有未完成的面试，请先提交评价或取消该面试")
    next_round = NEXT_ROUND.get(latest.get("round_name", ""))
    if not next_round:
        raise InvalidTransition("终面已经完成，不能继续安排下一轮")
    if latest.get("status") not in {"通过", "下一轮"}:
        raise InvalidTransition("上一轮面试尚未通过，不能安排下一轮")
    return next_round


def validate_schedule(current: str, interviews: list[dict], round_name: str) -> None:
    validate_round_name(round_name)
    if current not in {"待复核", "通过", "面试中"}:
        raise InvalidTransition(f"候选人当前为“{current}”，不能安排面试")
    expected = expected_next_round(interviews)
    if round_name != expected:
        raise InvalidTransition(f"当前应安排“{expected}”，不能直接安排“{round_name}”")


def feedback_target(round_name: str, conclusion: str) -> str:
    validate_round_name(round_name)
    if conclusion == "淘汰":
        return "淘汰"
    if conclusion == "待定":
        return "面试中"
    if conclusion == "下一轮":
        if round_name == "终面":
            raise InvalidTransition("终面不能选择“下一轮”")
        return "面试中"
    if conclusion == "通过":
        return "Offer待发" if round_name == "终面" else "面试中"
    raise InvalidTransition("面试结论必须是通过、淘汰、待定或下一轮")
