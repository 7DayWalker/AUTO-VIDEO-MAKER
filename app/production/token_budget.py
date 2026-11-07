"""Offline text sizing using the tokenizer vocabulary already shipped by Whisper.

UTF-8 bytes are not tokens: using one byte per token needlessly split ordinary
chapters after reserving 16K output. No tokenizer download or model load is
permitted here. GPT-2 BPE plus 25% headroom is a conservative sizing estimate,
not a claim about a gateway's undisclosed model tokenizer.
"""
import base64
from functools import lru_cache
import importlib.util
import math
from pathlib import Path


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken
        spec = importlib.util.find_spec("whisper")
        vocabulary = Path(spec.origin).parent / "assets" / "gpt2.tiktoken"
        with vocabulary.open(encoding="utf-8") as handle:
            ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in handle if line.strip())}
        return tiktoken.Encoding(name="avm-offline-budget", mergeable_ranks=ranks, special_tokens={},
            pat_str=r"'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+")
    except (ImportError, OSError, AttributeError, ValueError):
        return None


def text_token_budget(text):
    encoding = _encoding()
    if encoding is None:
        return len(text.encode("utf-8"))  # Safe, if less efficient, offline fallback.
    return math.ceil(len(encoding.encode(text, disallowed_special=())) * 1.25) + 32