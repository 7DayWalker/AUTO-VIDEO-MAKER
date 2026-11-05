"""Idempotent private proxy hardening; emits boolean checks only, never literals.

The proxy is an operator-owned Chat Completions -> Responses API bridge outside
Git. Preserve its routes/auth/tool handling while forwarding output budgets and
JSON mode, refusing media, and reporting incomplete upstream output truthfully.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess


_MEDIA_GUARD = """const data = JSON.parse(body);
        // AVM: fail closed before the upstream sees any binary/image content.
        const containsMedia = (value) => {
          if (typeof value === 'string') return /data:(?:image|video|audio)\\/[^,\\s]+;base64,[A-Za-z0-9+/=]{128,}|^[A-Za-z0-9+/]{1024,}={0,2}$/i.test(value);
          if (Array.isArray(value)) return value.some(containsMedia);
          if (value && typeof value === 'object') return /^(?:image_url|input_image|input_video|video_url)$/i.test(value.type || '') || Object.entries(value).some(([key, item]) => /^(?:image_url|input_image|inline_data|video_url)$/i.test(key) || containsMedia(item));
          return false;
        };
        if (containsMedia(data.messages) || containsMedia(data.input)) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: { code: 'media_payload_forbidden', message: 'This route accepts pure text only.' } }));
          return;
        }"""
_ENVELOPE = """model: data.model,
          input: input,
          ...(Number.isInteger(data.max_completion_tokens || data.max_tokens) ? { max_output_tokens: data.max_completion_tokens || data.max_tokens } : {}),
          ...(data.response_format && data.response_format.type === 'json_object' ? { text: { format: { type: 'json_object' } } } : {}),
          stream: isStream"""


def hardened_proxy_text(text):
    if "media_payload_forbidden" in text:
        # Replace this helper's complete guard, including a stale disabled guard.
        # Only actual media structures/long encoded payloads are blocked; code
        # discussions mentioning field names remain harmless pure-text traffic.
        guard = r"const data = JSON\.parse\(body\);\s*// AVM:[^\n]*\n\s*const containsMedia[\s\S]*?media_payload_forbidden[\s\S]*?\breturn;\s*\}"
        if len(re.findall(guard, text)) != 1:
            raise ValueError("Proxy media guard anchor not recognized; no private content displayed")
        text = re.sub(guard, lambda match: _MEDIA_GUARD, text, count=1)
    if "media_payload_forbidden" not in text:
        anchor = "const data = JSON.parse(body);"
        if text.count(anchor) != 1:
            raise ValueError("Proxy request anchor not recognized; no private content displayed")
        text = text.replace(anchor, _MEDIA_GUARD, 1)
    if "max_output_tokens:" not in text:
        pattern = r"model:\s*data\.model,\s*input:\s*input,\s*stream:\s*isStream"
        if len(re.findall(pattern, text)) != 1:
            raise ValueError("Proxy envelope anchor not recognized; no private content displayed")
        text = re.sub(pattern, lambda match: _ENVELOPE, text, count=1)
    if "reasoning: { effort: data.reasoning_effort }" not in text:
        anchor = "input: input,"
        if text.count(anchor) != 1:
            raise ValueError("Proxy reasoning anchor not recognized; no private content displayed")
        text = text.replace(anchor, anchor + "\n          ...(['none', 'low', 'medium', 'high'].includes(data.reasoning_effort) ? { reasoning: { effort: data.reasoning_effort } } : {}),", 1)
    text = text.replace("['low', 'medium', 'high'].includes(data.reasoning_effort)",
                        "['none', 'low', 'medium', 'high'].includes(data.reasoning_effort)")
    if "resp.status === 'incomplete'" not in text:
        pattern = r"finish_reason:\s*toolCalls\.length\s*>\s*0\s*\?\s*['\"]tool_calls['\"]\s*:\s*['\"]stop['\"]"
        if len(re.findall(pattern, text)) != 1:
            raise ValueError("Proxy completion anchor not recognized; no private content displayed")
        text = re.sub(pattern, "finish_reason: resp.status === 'incomplete' ? 'length' : toolCalls.length > 0 ? 'tool_calls' : 'stop'", text, count=1)
    return text


def main():
    path = Path.home() / "proxy.js"
    print("proxy_exists=" + str(path.is_file()))
    if not path.is_file():
        return 1
    try:
        original = path.read_text(encoding="utf-8-sig")
        modified = hardened_proxy_text(original)
    except (OSError, ValueError):
        print("proxy_structure_recognized=False")
        return 1
    print("proxy_structure_recognized=True")
    backup = path.with_name("proxy.js.avm-before-storyteller.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(modified, encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(path)], capture_output=True).returncode == 0
    print("proxy_syntax_valid=" + str(checked))
    if not checked:
        path.write_text(original, encoding="utf-8")
        return 1
    print("proxy_media_guard_installed=" + str("if (containsMedia(data.messages) || containsMedia(data.input))" in modified))
    print("proxy_token_budget_preserved=" + str("max_output_tokens:" in modified))
    print("proxy_json_mode_preserved=" + str("format: { type: 'json_object' }" in modified))
    print("proxy_incomplete_output_detected=" + str("resp.status === 'incomplete'" in modified))
    print("proxy_reasoning_effort_preserved=" + str("reasoning: { effort: data.reasoning_effort }" in modified))
    print("proxy_changed=" + str(original != modified))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())