"""Pure proxy upgrade; private content is never returned or logged by the CLI."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SETTINGS = """// AVM_REPURPOSE_SETTINGS: one validated model/reasoning/token contract.
function avmSettings(data) {
  const nested = data.reasoning?.effort;
  const flat = data.reasoning_effort;
  if (nested != null && flat != null && nested !== flat) throw new Error('Conflicting reasoning settings');
  const effort = nested ?? flat ?? 'low';
  if (!['none', 'low', 'medium', 'high'].includes(effort)) throw new Error('Invalid reasoning effort');
  const tokens = data.max_output_tokens ?? data.max_completion_tokens ?? data.max_tokens ?? 16384;
  if (!Number.isInteger(tokens) || tokens < 1 || tokens > 131072) throw new Error('Invalid output budget');
  return { reasoning: { ...(data.reasoning || {}), effort }, max_output_tokens: tokens };
}
function avmUsage(raw) {
  const usage = {};
  if (!raw || typeof raw !== 'object') return usage;
  for (const [from, to] of [['input_tokens', 'prompt_tokens'], ['output_tokens', 'completion_tokens'], ['total_tokens', 'total_tokens']]) {
    if (Number.isInteger(raw[from]) && raw[from] >= 0) usage[to] = raw[from];
  }
  for (const [from, to, field] of [['input_tokens_details', 'prompt_tokens_details', 'cached_tokens'], ['output_tokens_details', 'completion_tokens_details', 'reasoning_tokens']]) {
    const value = raw[from]?.[field];
    if (Number.isInteger(value) && value >= 0) usage[to] = { [field]: value };
  }
  return usage;
}
"""


def upgraded_proxy_text(text):
    if "AVM_REPURPOSE_SETTINGS" in text:
        return text
    if "upstreamModel" not in text:
        # Older operator bridge: retain tools/auth while restoring terminal SSE
        # semantics and declared model identity before installing v2 settings.
        from tools.configure_agentrouter_proxy import hardened_proxy_text
        text = hardened_proxy_text(text)
        text = text.replace("let isStream = false;", "let isStream = false;\n    let requestedModel = 'gpt-6.1-sol';", 1)
        text = text.replace("isStream = Boolean(data.stream);", "isStream = Boolean(data.stream);\n        requestedModel = data.model;", 1)
        text = text.replace("let buffer = '';", "let buffer = '';\n        let upstreamModel = null;\n        let terminal = false;", 1)
        text = text.replace("const evt = JSON.parse(dataStr);", """const evt = JSON.parse(dataStr);
              const declared = evt.response?.model;
              if (declared) {
                if (declared !== requestedModel || (upstreamModel && upstreamModel !== declared)) {
                  terminal = true;
                  res.end('data: ' + JSON.stringify({error:{code:'MODEL_MISMATCH',message:'Upstream model identity differs from request'}}) + '\\n\\n');
                  return;
                }
                upstreamModel = declared;
              }
              if (evt.type === 'response.failed' || evt.type === 'error' || evt.error) {
                terminal = true;
                res.end('data: ' + JSON.stringify({error:{code:'UPSTREAM_RESPONSE_FAILED',message:'Upstream reported a failed response'}}) + '\\n\\n');
                return;
              }
              if (evt.type === 'response.incomplete') {
                terminal = true;
                res.end('data: ' + JSON.stringify({model:upstreamModel,choices:[{index:0,delta:{},finish_reason:'length'}],usage:avmUsage(evt.response?.usage),incomplete_details:{reason:'max_output_tokens'}}) + '\\n\\ndata: [DONE]\\n\\n');
                return;
              }""", 1)
        text = text.replace("model: 'gpt-6.1-sol',", "model: upstreamModel,")
        text = text.replace("model: resp.model || 'gpt-6.1-sol',", "model: resp.model || requestedModel,", 1)
        text = text.replace("} else if (evt.type === 'response.completed') {", "} else if (evt.type === 'response.completed') {\n                terminal = true;", 1)
        text = text.replace("res.write('data: [DONE]\\n\\n');\n              }", "res.end('data: [DONE]\\n\\n');\n                return;\n              }", 1)
        text = text.replace("} catch (err) {}", """} catch (err) {
              terminal = true;
              res.end('data: ' + JSON.stringify({error:{code:'INVALID_UPSTREAM_EVENT',message:'Invalid upstream event'}}) + '\\n\\n');
              return;
            }""", 1)
        text = text.replace("proxyRes.on('end', () => res.end());", """proxyRes.on('end', () => {
          if (!res.writableEnded) res.end(terminal ? '' : 'data: ' + JSON.stringify({error:{code:'STREAM_INCOMPLETE',message:'Stream ended without completion'}}) + '\\n\\n');
        });""", 1)
        text = text.replace("if (!line.startsWith('data: ')) continue;", "if (terminal || res.writableEnded) return;\n            if (!line.startsWith('data:')) continue;", 1)
        text = text.replace("const dataStr = line.slice(6).trim();", "const dataStr = line.slice(5).trim();", 1)
        text = text.replace("proxyReq.setTimeout(300000", "proxyReq.setTimeout(660000", 1)
    anchors = {
        "http.createServer((req, res) => {": SETTINGS + "\nhttp.createServer((req, res) => {",
        "native.max_output_tokens ??= 16384;\n        native.reasoning ??= { effort: 'low' };":
            "Object.assign(native, avmSettings(native));\n        delete native.reasoning_effort;",
        "reasoning: data.reasoning || { effort: data.reasoning_effort || 'low' },\n          max_output_tokens: data.max_output_tokens || data.max_completion_tokens || data.max_tokens || 16384,":
            "...avmSettings(data),",
        "if (data.reasoning_effort) {\n          converted.reasoning = { effort: data.reasoning_effort };\n        }": "// Reasoning aliases were validated by avmSettings before dispatch.",
        "console.error('Request parse error:', e);":
            "res.writeHead(400, { 'Content-Type': 'application/json' });\n        res.end(JSON.stringify({ error: { code: 'invalid_planner_settings', message: 'Invalid JSON or conflicting planner settings.' } }));\n        return;",
        "model: upstreamModel,\n                  choices: [{ index: 0, delta: {}, finish_reason: sawToolCall ? 'tool_calls' : 'stop' }]":
            "model: upstreamModel,\n                  usage: avmUsage(evt.response?.usage),\n                  choices: [{ index: 0, delta: {}, finish_reason: sawToolCall ? 'tool_calls' : 'stop' }]",
        "model: resp.model || requestedModel,\n              choices:":
            "model: resp.model || requestedModel,\n              usage: avmUsage(resp.usage),\n              choices:",
    }
    for index, (anchor, replacement) in enumerate(anchors.items(), 1):
        if text.count(anchor) != 1:
            raise ValueError(f"Proxy upgrade anchor {index} not recognized; private content withheld")
        text = text.replace(anchor, replacement, 1)
    return text


def main():
    path = Path.home() / "proxy.js"
    try:
        original = path.read_text(encoding="utf-8-sig")
        updated = upgraded_proxy_text(original)
        result = subprocess.run(["node", "--check", "-"], input=updated, text=True, encoding="utf-8", capture_output=True, timeout=15)
        if result.returncode:
            print("proxy_upgrade_syntax_valid=False; no private content displayed")
            return 1
        if updated != original:
            backup = path.with_name("proxy.js.avm-before-v2-repair.bak")
            if not backup.exists():
                backup.write_text(original, encoding="utf-8")
            temporary = path.with_suffix(".js.avm-upgrade.tmp")
            temporary.write_text(updated, encoding="utf-8")
            temporary.replace(path)
        print("proxy_upgrade_syntax_valid=True; proxy_changed=" + str(updated != original))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("proxy_upgrade_failed; private content withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())