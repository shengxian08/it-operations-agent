"""Object selection only: persisted identifiers never grant permission to a ticket."""

import re
from dataclasses import dataclass
from typing import Any


MAX_CANDIDATES = 20
TICKET_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z\d_-])IT-[0-9]{4}-[0-9]{4,}(?![A-Za-z\d_-])",
    re.IGNORECASE,
)
_ORDINAL = re.compile(r"^(?:请查|查询|查|选|选择)?\s*第([0-9]+|[一二两三四五六七八九十]+)(?:张|个|单|条|个工单|张工单)?[。！？!?\s]*$")
_NUMBER_LIKE = re.compile(r"IT-[A-Za-z\d_-]*", re.IGNORECASE)
_NUMBER_HINT = re.compile(r"IT-(?=\d|$|[\s，,。；;！？!?])", re.IGNORECASE)
_HISTORY_REFERENCE = re.compile(r"(?:上次|上一(?:张|个|单)|刚才|刚刚|那(?:张|个)|这(?:张|个))")
_OTHER_OBJECT = re.compile(r"(?:另(?:外)?一(?:张|个|单)|其他工单|别的工单|新(?:的)?工单)")
_CANCEL = re.compile(r"^(?:算了[，,\s]*)?(?:不查了|不用查了?|取消查询(?:工单)?|取消查单|算了)[。！？!?\s]*$")
_FOLLOW_UP = re.compile(r"^(?:那张|这个|它|这张|上一张)?(?:工单)?(?:现在|目前)?(?:的)?(?:进度呢|进度怎么样|进度如何|状态呢|处理到哪了|处理到哪一步了|处理完了吗|怎么样了)[。！？!?\s]*$")


def ticket_numbers(message: str) -> list[str]:
    return list(dict.fromkeys(
        match.group(0).upper() for match in TICKET_NUMBER_PATTERN.finditer(message)
        if len(match.group(0)) <= 50
    ))


def valid_number(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 50
            and TICKET_NUMBER_PATTERN.fullmatch(value) is not None
            and value == value.upper())


def _malformed_number(message: str) -> bool:
    for match in _NUMBER_LIKE.finditer(message):
        previous = message[match.start() - 1] if match.start() else ""
        glued = previous and (previous.isdecimal() or (previous.isascii() and (previous.isalnum() or previous in "_-")))
        if glued or not valid_number(match.group(0).upper()):
            return True
    return False


def empty_context(user_id: str, conversation_id: str) -> dict[str, Any]:
    return {"user_id": user_id, "conversation_id": conversation_id, "candidates": [],
            "pending_candidates": None, "recent_lookup": False, "overflow": False}


def owned_context(value: object, user_id: str, conversation_id: str) -> dict[str, Any]:
    empty = empty_context(user_id, conversation_id)
    if (not isinstance(value, dict) or value.get("user_id") != user_id
            or value.get("conversation_id") != conversation_id):
        return empty
    candidates = value.get("candidates")
    pending = value.get("pending_candidates")
    if (not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES
            or any(not valid_number(number) for number in candidates)
            or len(set(candidates)) != len(candidates)
            or type(value.get("recent_lookup")) is not bool
            or type(value.get("overflow")) is not bool):
        return empty
    if pending is not None and (not isinstance(pending, list) or len(pending) > MAX_CANDIDATES
            or any(number not in candidates for number in pending)
            or len(set(pending)) != len(pending)):
        return empty
    return {**empty, **value, "candidates": list(candidates),
            "pending_candidates": list(pending) if pending is not None else None}


def is_lookup_message(message: str, context: dict[str, Any]) -> bool:
    if ticket_numbers(message) or (_NUMBER_HINT.search(message) and _malformed_number(message)):
        return True
    if "工单" in message and (_HISTORY_REFERENCE.search(message) or any(word in message for word in (
        "查询", "查单", "查一下", "状态", "进度", "上次", "上一张", "那张", "处理到", "处理完", "取消查询",
    ))):
        return True
    if context["recent_lookup"]:
        return bool(_FOLLOW_UP.fullmatch(message.strip())
                    or (context["pending_candidates"] is not None
                        and (_ORDINAL.fullmatch(message.strip()) or _CANCEL.search(message))))
    return False


def _ordinal(message: str) -> int | None:
    match = _ORDINAL.fullmatch(message.strip())
    if match is None:
        return None
    value = match.group(1)
    if value.isascii() and value.isdigit():
        return int(value) if len(value) < 4 else 0
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if "十" in value and value.count("十") == 1:
        tens, units = value.split("十")
        if (not tens or tens in digits) and (not units or units in digits):
            return (digits.get(tens, 1) * 10) + digits.get(units, 0)
    return 0


@dataclass(frozen=True)
class LookupSelection:
    ticket_number: str | None = None
    basis: str | None = None
    reason: str | None = None
    candidates: tuple[str, ...] = ()
    cancelled: bool = False


def select_ticket(message: str, context: dict[str, Any]) -> LookupSelection:
    if _CANCEL.search(message) and ("工单" in message or context["pending_candidates"] is not None):
        return LookupSelection(cancelled=True)
    explicit = ticket_numbers(message)
    if _malformed_number(message):
        # Invalid explicit input must not silently choose an older conversation object.
        return LookupSelection(reason="missing_ticket_number", candidates=tuple(explicit[:MAX_CANDIDATES]))
    if len(explicit) == 1:
        return LookupSelection(explicit[0], "explicit")
    if len(explicit) > 1:
        return LookupSelection(reason="too_many_candidates" if len(explicit) > MAX_CANDIDATES else "ambiguous_ticket_number",
                               candidates=tuple(explicit[:MAX_CANDIDATES]))
    if _OTHER_OBJECT.search(message):
        return LookupSelection(reason="missing_ticket_number")
    candidates = context["candidates"]
    pending = context["pending_candidates"]
    if context["overflow"]:
        return LookupSelection(reason="too_many_candidates", candidates=tuple(candidates))
    ordinal = _ordinal(message)
    if ordinal is not None and pending is not None:
        if 1 <= ordinal <= len(pending):
            return LookupSelection(pending[ordinal - 1], "clarification_selection")
        return LookupSelection(reason="ambiguous_ticket_number" if pending else "missing_ticket_number",
                               candidates=tuple(pending))
    refers_to_history = ("工单" in message and _HISTORY_REFERENCE.search(message)) or (
        context["recent_lookup"] and _FOLLOW_UP.fullmatch(message.strip()))
    if len(candidates) == 1 and refers_to_history:
        return LookupSelection(candidates[0], "conversation")
    if len(candidates) == 1:
        return LookupSelection(reason="missing_ticket_number", candidates=tuple(candidates))
    return LookupSelection(reason="ambiguous_ticket_number" if candidates else "missing_ticket_number",
                           candidates=tuple(candidates))


def lookup_record(user_id: str, conversation_id: str, selection: LookupSelection,
                  outcome: str) -> dict[str, Any]:
    return {"schema_version": 1, "user_id": user_id, "conversation_id": conversation_id,
            "outcome": outcome, "ticket_number": selection.ticket_number,
            "basis": selection.basis, "reason": selection.reason,
            "candidates": list(selection.candidates)}


def clarification_answer(selection: LookupSelection) -> str:
    if selection.reason == "too_many_candidates":
        return "这段对话涉及的工单较多，请提供要查询的完整工单号（如 IT-2026-0001）。"
    if not selection.candidates:
        return "请提供要查询的完整工单号（如 IT-2026-0001），我会查询当前进度。"
    if len(selection.candidates) == 1:
        return f"请确认要查询的是工单 {selection.candidates[0]}，或提供另一张工单的完整编号。"
    choices = "；".join(f"第{index}张：{number}" for index, number in enumerate(selection.candidates, 1))
    return f"有多张可能的工单，请选择要查询的一张，或提供完整工单号。{choices}。"
