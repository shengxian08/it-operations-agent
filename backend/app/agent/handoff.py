"""Explicit employee actions, excluding quoted instructions and denials."""
import re


_REQUEST = re.compile(
    r"(?:(?:请(?:帮我)?|麻烦(?:帮我)?|帮我)?(?:转(?:接)?(?:到)?人工(?:支持|客服)?|联系人工(?:支持|客服)|找人工(?:支持|客服)?)"
    r"|(?:我要|我想要?|我?需要)(?:转(?:接)?(?:到)?)?人工(?:支持|客服)?|(?:请|麻烦)人工(?:支持|客服)?)"
)
_DENIAL = re.compile(r"(?:暂时|现在)?(?:不要|不用|不想|无需|别|暂不|不需要).*(?:转.*人工|联系人工|人工客服|人工支持)")


def requests_handoff(message: str) -> bool:
    text = re.sub(r"```.*?```|`[^`]*`|“[^”]*”|‘[^’]*’|\"[^\"]*\"|'[^']*'", "", message, flags=re.S)
    requested = False
    for clause in re.split(r"[，,。.!！?？；;\n]+", text):
        clause = clause.strip()
        if _DENIAL.fullmatch(clause):
            requested = False
        else:
            match = _REQUEST.match(clause)
            if match and not re.search(r"是什么意思|是什么|为什么", clause):
                tail = clause[match.end():].strip()
                if tail in ("", "一下", "谢谢", "帮忙") or tail.startswith(("帮我", "协助", "处理", "跟进", "解决", "排查", "接手", "看一下")):
                    requested = True
    return requested
