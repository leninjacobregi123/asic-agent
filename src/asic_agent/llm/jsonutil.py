"""Pull a JSON object out of a model response."""

from __future__ import annotations

import json
import re

from .errors import LLMError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose or code fences regardless of instructions, and a
    pipeline that crashes on that is a pipeline that fails during the demo. Try
    the last fence, then the raw text, then the LAST balanced {...} span —
    a model that drafts an answer and then corrects itself means the final one.
    """
    for m in reversed(_FENCE.findall(text)):
        try:
            return json.loads(m.strip())
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    found = None
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            found = obj
                            start = i  # continue after this object
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)

    if found is not None:
        return found
    raise LLMError(f"no JSON object found in response: {text[:300]}")
