"""Prompts that load from YAML, never from a Python string literal.

    from prompts.loader import load_prompts
    P = load_prompts("rag");  P["rag_prompt"]

WHY YAML
    Prompt text changes most often, a non-engineer may need to read it, and an edit
    to the desk's tone should not be able to break the program. Each prompt carries
    a `<name>_version` beside it, and every model call is stamped with that version
    and a hash of the assembled text — so a trace says which wording produced it.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent
FEATURES = ("route", "rag", "credit", "memory")


@lru_cache
def load_prompts(feature: str) -> dict:
    if feature not in FEATURES:
        raise KeyError(f"no prompts/{feature}.yml: the features are {', '.join(FEATURES)}")
    path = PROMPTS_DIR / f"{feature}.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    for f in FEATURES:
        p = load_prompts(f)
        names = [k for k in p if k.endswith("_prompt")]
        print(f"prompts/{f}.yml")
        for n in names:
            print(f"   {n:22s} {p.get(n + '_version', 'unversioned')}")
