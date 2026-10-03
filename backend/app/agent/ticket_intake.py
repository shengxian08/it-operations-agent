"""Bounded employee facts for draft preparation; never infer absent facts."""
import re
from typing import Any

from app.schemas import TicketDraft


FIELDS = ("problem", "impact", "attempted_steps")
LIMITS = {"problem": 2000, "impact": 1000}
_CREATE = re.compile(r"(?:请|帮我|麻烦)?(?:创建|新建|提交|建)(?:一个)?(?:工单|单)|(?:please\s+)?(?:create|open|submit)(?:\s+a)?\s+ticket", re.I)
_LABEL = re.compile(r"(?P<label>问题|故障(?:现象)?|影响(?:范围)?|已尝试(?:操作|步骤)?|尝试操作)\s*[：:]")
_QUOTED = re.compile(r'''“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|'[^']*'|`[^`]*`''', re.S)
_CANCEL = re.compile(r"^(?:请)?(?:取消(?:建单|创建工单|工单创建)?|算了|不用了|(?:算了[，,、 ]*)?不(?:要)?(?:创建|新建|建|提交)(?:一个)?(?:工单|单)?(?:了)?)[。！!？? ]*$")
_EMPTY_ATTEMPTS = re.compile(r"^(?:尚未尝试|未尝试(?:任何操作)?|没有(?:尝试过|尝试|操作)|没(?:尝试过|试过)|无|none|not tried)[。.!！ ]*$", re.I)
_CONTROL = {"好的", "好", "确认", "确认提交", "提交", "嗯", "谢谢", "不知道", "不清楚", "不知道影响范围", "ok", "yes"}
_QUESTION = re.compile(r"如何|怎么办|怎么(?:做|设置|连接|解决|操作|安装)|是什么|为什么|工作时间|操作步骤", re.I)


def _creation_match(message: str):
    if _CANCEL.fullmatch(message.strip()):
        return None
    if re.match(r"^(?:请问)?(?:如何|怎么|怎样|how\s+(?:to|do))", message.strip(), re.I):
        return None
    candidate = _QUOTED.sub(lambda match: " " * len(match.group()), message)
    label = _LABEL.search(candidate)
    if label:
        candidate = candidate[:label.start()]
    return _CREATE.search(candidate)


def is_creation(message: str) -> bool:
    return _creation_match(message) is not None


def _without_creation(message: str) -> str:
    match = _creation_match(message)
    return message[:match.start()] + message[match.end():] if match else message


def _meaningful(value: str) -> bool:
    content = value.strip(" \t\r\n。，,;；、!！?？:.：").casefold()
    return bool(content) and content not in _CONTROL and not _CANCEL.fullmatch(content)


def _fact(value: object, field: str) -> bool:
    return (isinstance(value, str) and 2 <= len(value) <= LIMITS[field]
            and value.strip() == value and _meaningful(value)
            and not _CREATE.fullmatch(value))


def _steps(value: object) -> bool:
    return (isinstance(value, list) and len(value) <= 10
            and all(isinstance(step, str) and 2 <= len(step) <= 300 and step.strip() == step and _meaningful(step) for step in value))


def owned_intake(value: object, user_id: str, conversation_id: str) -> dict[str, Any] | None:
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("user_id") != user_id
            or value.get("conversation_id") != conversation_id or value.get("outcome") != "collecting"):
        return None
    if not set(("schema_version", "user_id", "conversation_id", "outcome", *FIELDS, "next_field", "reason")).issubset(value):
        return None
    for field in FIELDS:
        item = value.get(field)
        if item is not None and not (_steps(item) if field == "attempted_steps" else _fact(item, field)):
            return None
    missing = next((field for field in FIELDS if value.get(field) is None), None)
    if missing is None or value.get("next_field") != missing or value.get("reason") not in (None, "invalid_field", "field_too_long"):
        return None
    return {key: value[key] for key in ("schema_version", "user_id", "conversation_id", "outcome", *FIELDS, "next_field", "reason")}


def has_intake_fields(message: str) -> bool:
    return bool(_LABEL.search(message))


def is_intake_followup(message: str, context: dict[str, Any] | None) -> bool:
    if context is None:
        return False
    if _LABEL.search(message) or _CANCEL.fullmatch(message.strip()):
        return True
    if any(word in message for word in ("转人工", "人工支持", "人工客服")):
        return False
    return not bool(_QUESTION.search(message))


def _labeled(message: str) -> dict[str, str]:
    matches = list(_LABEL.finditer(message))
    values = {}
    for index, match in enumerate(matches):
        label = match.group("label")
        field = "problem" if label.startswith(("问题", "故障")) else "impact" if label.startswith("影响") else "attempted_steps"
        end = matches[index + 1].start() if index + 1 < len(matches) else len(message)
        values[field] = message[match.end():end].strip(" \r\n，,；;。")
    return values


def collect_intake(message: str, context: dict[str, Any] | None, user_id: str, conversation_id: str) -> dict[str, Any]:
    record = {"schema_version": 1, "user_id": user_id, "conversation_id": conversation_id,
              "outcome": "collecting", "problem": None, "impact": None, "attempted_steps": None,
              "next_field": "problem", "reason": None}
    if context is not None and not is_creation(message):
        record.update({field: context[field] for field in FIELDS})
    if context is not None and _CANCEL.fullmatch(message.strip()):
        return {**record, "outcome": "cancelled", "next_field": None}
    updates = _labeled(message)
    if not updates:
        text = _without_creation(message).strip(" \r\n，,。；;：:")
        field = "problem" if is_creation(message) or context is None else context["next_field"]
        if text:
            updates[field] = text
    elif is_creation(message) and "problem" not in updates:
        prefix = _without_creation(message[:_LABEL.search(message).start()]).strip(" \r\n，,。；;：:")
        if prefix:
            updates["problem"] = prefix
    accepted = {}
    for field, raw in updates.items():
        if field == "attempted_steps":
            value = [] if _EMPTY_ATTEMPTS.fullmatch(raw) else [part.strip() for part in re.split(r"[；;\n]+", raw) if part.strip()]
            if len(value) > 10 or any(len(step) > 300 for step in value):
                record["reason"] = "field_too_long"
                break
            if (not raw or raw.strip(" 。.!！?？").casefold() in _CONTROL or not _steps(value)):
                record["reason"] = "invalid_field"
                break
        else:
            value = raw
            if len(raw) > LIMITS[field]:
                record["reason"] = "field_too_long"
                break
            if not _fact(raw, field):
                record["reason"] = "invalid_field"
                break
        accepted[field] = value
    if record["reason"] is None:
        record.update(accepted)
    missing = next((field for field in FIELDS if record[field] is None), None)
    record["next_field"] = missing
    if missing is None and record["reason"] is None:
        record["outcome"] = "ready"
    return record


def intake_priority(problem: str, impact: str) -> str:
    if any(word in problem for word in ("安全事件", "设备丢失")):
        return "critical"
    if any(word in impact for word in ("全公司", "全员", "全部中断", "无法办公", "业务中断")):
        return "high"
    return "medium"


def draft_facts_complete(value: object) -> bool:
    try:
        draft = value if isinstance(value, TicketDraft) else TicketDraft.model_validate(value)
    except (ValueError, TypeError):
        return False
    return (type(draft.intake_version) is int and draft.intake_version == 1
            and "attempted_steps" in draft.model_fields_set
            and _fact(draft.problem, "problem") and _fact(draft.impact, "impact")
            and _steps(list(draft.attempted_steps))
            and draft.description == f"{draft.problem}\n影响范围：{draft.impact}")


def intake_answer(record: dict[str, Any]) -> str:
    if record["outcome"] == "cancelled":
        return "已取消本次工单信息收集。"
    questions = {"problem": "请描述具体故障现象或错误信息，以便准备工单。",
                 "impact": "请说明影响范围，以及是否阻断工作（例如仅本人、部门或全公司）。",
                 "attempted_steps": "请说明已经尝试的操作及结果；尚未操作可明确回复“尚未尝试”。"}
    prefix = "本次字段超过长度或条数上限，请缩短后重新提供。" if record["reason"] == "field_too_long" else ""
    return prefix + questions[record["next_field"]]
