"""Helpers for the background language-model jobs (the question index, the key-fact sheet): reading the JSON the hosted
model returns when it slips (code fences, text around the JSON, trailing commas, a reply cut off before its closing
brackets), and the strict-JSON instruction those jobs add to their prompt. The calls themselves go through
services/llm.py like every other model call."""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

STRICT_JSON_NOTE = ("Reply with exactly one JSON object and nothing else: no code fences, no comments, no text before or "
                    "after it. Use double quotes for every key and string. Write Hindi in Devanagari script.")


def strict_messages(messages: List[dict]) -> List[dict]:
    """The same messages with the strict-JSON note added to the last system message."""
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") == "system":
            m["content"] = (m.get("content") or "") + "\n\n" + STRICT_JSON_NOTE
            return out
    return [{"role": "system", "content": STRICT_JSON_NOTE}, *out]


# ------------------------------------------------------------------------------------------ JSON repair

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def _close_truncated(text: str) -> str:
    """Close what a cut-off reply left open: an unfinished string, then every open bracket (innermost first)."""
    stack: List[str] = []
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    out = text + ('"' if in_str else "")
    out = re.sub(r"[,:\s]+$", "", out) if not in_str else out
    return out + "".join(reversed(stack))


def parse_json(text: Any) -> Any:
    """A JSON object or list from a model's reply, repairing the usual slips: code fences, text around the JSON,
    trailing commas, curly quotes as delimiters, control characters, a reply cut off before its closing brackets.
    Raises ValueError when nothing usable is left."""
    t = _FENCE.sub("", str(text or "")).strip()
    t = "".join(c for c in t if c >= " " or c in "\n\t")
    if not t:
        raise ValueError("empty reply")
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("model reply contained no JSON")
    t = t[min(starts):]
    candidates = [t]
    end = max(t.rfind("}"), t.rfind("]"))
    if end > 0:
        candidates.append(t[:end + 1])
    for base in list(candidates):
        candidates.append(re.sub(r",\s*([}\]])", r"\1", base))
        candidates.append(re.sub(r",\s*([}\]])", r"\1", base.replace("“", '"').replace("”", '"')))
    candidates.append(re.sub(r",\s*([}\]])", r"\1", _close_truncated(t)))
    last: Optional[Exception] = None
    for c in candidates:
        try:
            out = json.loads(c)
        except ValueError as e:
            last = e
            continue
        if isinstance(out, (dict, list)):
            return out
    raise ValueError("unreadable JSON: %s" % last)
