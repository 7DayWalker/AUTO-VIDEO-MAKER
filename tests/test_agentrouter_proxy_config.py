"""Proxy configuration hardening on synthetic source only; no private file IO."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tools.configure_agentrouter_proxy import hardened_proxy_text


SOURCE = """
function adapt(body) {
  const data = JSON.parse(body);
  const isStream = Boolean(data.stream);
  const input = data.messages;
  const converted = { model: data.model, input: input, stream: isStream };
  return converted;
}
function finish(resp, toolCalls) {
  return { finish_reason: toolCalls.length > 0 ? 'tool_calls' : 'stop' };
}
"""


def test_proxy_hardening_preserves_route_identity_and_is_idempotent():
    changed = hardened_proxy_text(SOURCE)
    assert changed == hardened_proxy_text(changed)
    assert "model: data.model" in changed and "const input = data.messages" in changed
    assert "media_payload_forbidden" in changed and "max_output_tokens:" in changed
    assert "format: { type: 'json_object' }" in changed
    assert "resp.status === 'incomplete' ? 'length'" in changed


def test_ambiguous_proxy_is_not_modified():
    with pytest.raises(ValueError): hardened_proxy_text("not a recognized proxy")


def test_proxy_bridge_executes_with_json_budgets_and_never_forwards_synthetic_media():
    if not shutil.which("node"):
        pytest.skip("Node is required to execute the synthetic proxy bridge")
    code = hardened_proxy_text(SOURCE) + """
const accepted = adapt(JSON.stringify({model:'gpt-6.1-sol', messages:[{role:'user', content:'Discuss data:image/jpeg;base64,AAAA, image_url and input_image in code; no image attached.'}],
  max_tokens:8192, response_format:{type:'json_object'}, reasoning_effort:'low', stream:false}));
let rejected = false;
const res = {writeHead:(status)=>{rejected=status===400;}, end:()=>{}};
adapt(JSON.stringify({messages:[{role:'user', content:[{type:'input_image', image_url:'synthetic-never-transmitted'}]}]}));
console.log(JSON.stringify({model:accepted.model, tokens:accepted.max_output_tokens,
  format:accepted.text.format.type, effort:accepted.reasoning.effort, incomplete:finish({status:'incomplete'},[]).finish_reason, rejected}));
"""
    result = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"model": "gpt-6.1-sol", "tokens": 8192, "format": "json_object",
                                      "effort": "low", "incomplete": "length", "rejected": True}


def test_disabled_stale_guard_is_reenabled_without_rejecting_plain_code_text():
    hardened = hardened_proxy_text(SOURCE)
    disabled = hardened.replace("if (containsMedia(data.messages) || containsMedia(data.input))", "if (false)")
    assert hardened_proxy_text(disabled) == hardened


def test_proxy_never_treats_a_plain_string_as_an_attachment():
    code = hardened_proxy_text(SOURCE) + """
const res = {writeHead:()=>{throw new Error('text was blocked');},end:()=>{}};
const values = ['data:image/png;base64,' + 'A'.repeat(2048), 'image_url input_image', 'A'.repeat(4096)];
console.log(JSON.stringify(values.map(content => adapt(JSON.stringify({messages:[{role:'user',content}]})).input[0].content === content)));
"""
    result = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == [True, True, True]


def test_proxy_defaults_to_16k_low_and_preserves_nested_responses_reasoning():
    code = hardened_proxy_text(SOURCE) + """
const defaults = adapt(JSON.stringify({model:'gpt-6.1-sol',messages:[]}));
const explicit = adapt(JSON.stringify({model:'gpt-6-astra',messages:[],max_output_tokens:16384,reasoning:{effort:'low'}}));
console.log(JSON.stringify([defaults.max_output_tokens, defaults.reasoning, explicit.max_output_tokens, explicit.reasoning]));
"""
    result = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == [16384, {"effort": "low"}, 16384, {"effort": "low"}]