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
          if (typeof value === 'string') return false;
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
          max_output_tokens: data.max_output_tokens || data.max_completion_tokens || data.max_tokens || 16384,
          ...(data.response_format && data.response_format.type === 'json_object' ? { text: { format: { type: 'json_object' } } } : {}),
          stream: isStream"""


def hardened_proxy_text(text):
    if "media_payload_forbidden" in text:
        # Replace this helper's complete guard, including a stale disabled guard.
        # Only attachment objects are blocked. All plain strings, including
        # code examples mentioning data:image or input_image, remain text.
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
    # Upgrade older installations without displaying private proxy literals.
    text = re.sub(r"\.\.\.\(Number\.isInteger\(data\.max_completion_tokens \|\| data\.max_tokens\) \? \{ max_output_tokens: data\.max_completion_tokens \|\| data\.max_tokens \} : \{\}\),",
                  "max_output_tokens: data.max_output_tokens || data.max_completion_tokens || data.max_tokens || 16384,", text)
    old_reasoning = r"\.\.\.\(\['(?:none', ')?low', 'medium', 'high'\]\.includes\(data\.reasoning_effort\) \? \{ reasoning: \{ effort: data\.reasoning_effort \} \} : \{\}\),"
    reasoning = "reasoning: data.reasoning || { effort: data.reasoning_effort || 'low' },"
    text = re.sub(old_reasoning, reasoning, text)
    if reasoning not in text:
        anchor = "input: input,"
        if text.count(anchor) != 1:
            raise ValueError("Proxy reasoning anchor not recognized; no private content displayed")
        text = text.replace(anchor, anchor + "\n          " + reasoning, 1)
    # Native /v1/responses is not translated: preserve its reasoning object and
    # apply safe defaults only when absent. Other private proxy routes stay as-is.
    anchor = "let payload = body;"
    if anchor in text and "AVM_NATIVE_RESPONSES" not in text:
        text = text.replace(anchor, anchor + """
    // AVM_NATIVE_RESPONSES: no reasoning/token fields dropped in pass-through.
    if (!isChat && /\\/responses(?:\\?|$)/.test(req.url) && body) {
      try {
        const native = JSON.parse(body);
        native.max_output_tokens ??= 16384;
        native.reasoning ??= { effort: 'low' };
        payload = JSON.stringify(native);
      } catch { res.writeHead(400); res.end(); return; }
    }
""", 1)
    # This is a text JSON bridge, never a movie-upload proxy. Bound JSON RAM.
    if "req.on('data', chunk => body += chunk);" in text:
        text = text.replace("req.on('data', chunk => body += chunk);", """let bodyBytes = 0;
  req.on('data', chunk => {
    bodyBytes += chunk.length;
    if (bodyBytes > 8 * 1024 * 1024) { res.writeHead(413); res.end(); req.destroy(); return; }
    body += chunk;
  });""", 1)
    if "proxyReq.setTimeout(300000" not in text and "proxyReq.write(payload);" in text:
        text = text.replace("proxyReq.write(payload);", "proxyReq.setTimeout(300000, () => proxyReq.destroy(new Error('Upstream socket timed out')));\n    proxyReq.write(payload);", 1)
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
    print("proxy_reasoning_effort_preserved=" + str("reasoning: data.reasoning" in modified))
    print("proxy_changed=" + str(original != modified))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())