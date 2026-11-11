"""Synthetic JS only: never read private proxy/credentials or connect upstream."""
import json
import subprocess

from tools.repurpose_proxy_settings import SETTINGS


def test_reasoning_aliases_budget_and_usage_are_validated_offline():
    code = SETTINGS + """
const results = [];
for (const model of ['gpt-6.1-sol','gpt-6-astra']) {
  for (const effort of ['low','medium','high']) {
    for (const settings of [{reasoning:{effort}}, {reasoning_effort:effort}]) {
      const budget = effort === 'low' ? 16384 : 32768;
      results.push(avmSettings({model,...settings,max_tokens:budget}));
    }
  }
}
let rejected = 0;
for (const data of [{reasoning:{effort:'high'},reasoning_effort:'low'}, {reasoning_effort:'other'}, {max_tokens:0}]) {
  try { avmSettings(data); } catch { rejected++; }
}
console.log(JSON.stringify({results,rejected,usage:avmUsage({input_tokens:10,output_tokens:20,total_tokens:30,output_tokens_details:{reasoning_tokens:12}})}));
"""
    result = subprocess.run(["node", "-"], input=code, text=True, capture_output=True, check=True, timeout=15)
    data = json.loads(result.stdout)
    assert len(data["results"]) == 12 and data["rejected"] == 3
    assert [r["max_output_tokens"] for r in data["results"]] == [16384, 16384, 32768, 32768, 32768, 32768] * 2
    assert data["usage"]["completion_tokens_details"] == {"reasoning_tokens": 12}