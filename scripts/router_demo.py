#!/usr/bin/env python3
"""End-to-end router demo on one text: the company's LoRA student finds the
personal values, the router swaps them for fakes, the user's chosen model
answers on the masked text, and the router puts the real values back.

Usage: router_demo.py "<question about a text>"   (needs FIREWORKS_API_KEY)
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pii_prompt import PROMPT  # noqa: E402
from score_fireworks import parse  # noqa: E402

URL = "https://api.fireworks.ai/inference/v1/chat/completions"
STUDENT = "accounts/renan-ym76yzx07ofs/models/pii-gold-glm53"
USER_MODEL = os.environ.get("USER_MODEL", "accounts/fireworks/models/kimi-k3")


def chat(model, messages):
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 1500, "messages": messages}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json",
                                             "Authorization": "Bearer " + os.environ["FIREWORKS_API_KEY"]})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def main():
    text = sys.argv[1]
    print("1. YOU ASK\n" + text + "\n")
    found = parse(chat(STUDENT, [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}])) or []
    print("2-3. COMPANY MODEL FOUND\n" + json.dumps(found, ensure_ascii=False) + "\n")
    fakes, masked = {}, text
    for i, e in enumerate(sorted(found, key=lambda e: -len(e["value"]))):
        fake = f"[{e['label']}_{i + 1}]"
        if e["value"] in masked:
            fakes[fake] = e["value"]
            masked = masked.replace(e["value"], fake)
    print("4. MASKED TEXT SENT OUT\n" + masked + "\n")
    answer = re_strip(chat(USER_MODEL, [{"role": "user", "content": masked}]))
    print("5. YOUR MODEL ANSWERED (sees fakes only)\n" + answer + "\n")
    for fake, real in fakes.items():
        answer = answer.replace(fake, real)
    print("6. ROUTER PUTS REAL VALUES BACK\n" + answer)


def re_strip(s):
    import re
    return re.sub(r"(?s)<think>.*?</think>", "", s).strip()


if __name__ == "__main__":
    main()
