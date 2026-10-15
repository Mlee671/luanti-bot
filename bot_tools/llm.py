"""Ask an OpenAI-compatible chat API which tool to use next.

Standard library only, like the rest of the project. Configuration comes from
the environment, with defaults matching Api_gateway/Api.py:

    LLM_BASE_URL   default https://agent.elliottwen.info/v1
    LLM_MODEL      default MiniMax-M3
    LLM_API_KEY    default: contents of Api_gateway/Api-Key.txt
"""
import json
import os
import re
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.environ.get("LLM_BASE_URL", "https://agent.elliottwen.info/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "MiniMax-M3")


def _api_key():
    key = os.environ.get("LLM_API_KEY")
    if key:
        return key.strip()
    path = os.path.join(ROOT, "Api_gateway", "Api-Key.txt")
    if not os.path.exists(path):
        raise RuntimeError(f"no API key: set LLM_API_KEY or put the key in {path}")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # accept a bare key or a line like  "key" : "sk-..."
    match = re.search(r"sk-[A-Za-z0-9_\-]+", text)
    return match.group(0) if match else text.strip().strip('"')


def chat(messages, tools, timeout=120):
    """POST one chat completion request and return the assistant message."""
    body = {"model": MODEL, "messages": messages, "tools": tools,
            "tool_choice": "auto", "temperature": 0.2}
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 # Cloudflare in front of the gateway rejects urllib's default
                 # "Python-urllib/3.x" agent with "error code: 1010"
                 "User-Agent": "Mozilla/5.0 (compatible; luantibot-agent/0.1)",
                 "Authorization": "Bearer " + _api_key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode(errors='replace')[:500]}")
    return data["choices"][0]["message"]


def parse_choice(message):
    """Pull (tool_name, args, reasoning) out of an assistant message.

    Prefers a native function call; falls back to a JSON object such as
    {"tool": "craft", "args": {...}} in the text, for models that ignore `tools`.
    """
    text = message.get("content") or ""
    reasoning = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0]["function"]
        raw = fn.get("arguments") or "{}"
        args = json.loads(raw) if isinstance(raw, str) else raw
        return fn["name"], args, reasoning
    match = re.search(r"\{.*\}", reasoning, flags=re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
            if "tool" in obj:
                return obj["tool"], obj.get("args", {}), reasoning
        except json.JSONDecodeError:
            pass
    return None, {}, reasoning
