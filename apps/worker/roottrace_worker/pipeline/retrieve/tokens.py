"""Token estimation for the 24,000-token hard budget (`03` §S5, P3, T4.4).

**No tokenizer library, by a reasoned choice made without the coordinator
present** (the same authority already used for `ast` over Tree-sitter at
T4.1/T4.2/T4.3, extended here) — and for a structural reason specific to this
system rather than only a dependency-avoidance preference. `06` §2.2 routes
every tier across **two providers** (`anthropic` primary, `openai` failover,
reversed for the critic tier deliberately at `06` §2.3 so the reviewer is
never the same vendor as the author). No single tokenizer is exact for both:
`tiktoken` matches OpenAI's BPE exactly and is only an approximation for
Claude, and Anthropic ships no offline tokenizer at all — its only official
counting path is a network call, which a hot pipeline stage must not make and
which P3 forbids depending on for something this fundamental. Any library
choice would therefore still be an estimate for at least one provider in
every tier; adding a compiled dependency to get exactness for the vendor that
is not the one actually serving the call is a poor trade.

**The estimate is deliberately biased to overcount, not to be closest on
average.** A hard 24,000-token budget (P3) is a ceiling a live provider will
reject a request for exceeding — the failure mode of undercounting is a
prompt that silently exceeds what was budgeted; the failure mode of
overcounting is evicting slightly more context than strictly necessary. The
second is recoverable (T4.4's own `insufficient_context` path exists for
exactly this) and the first is not, so the estimate leans conservative.

`3.5` characters per token is the constant. Code skews denser than English
prose (more punctuation, operators, and identifier boundaries), and BPE
tokenizers commonly land Python source in the 3-4 chars/token range; `3.5`
sits at the dense end of that range on purpose, which is what makes the bias
overcounting rather than a coin flip.
"""

from __future__ import annotations

import math

#: See module docstring for the reasoning. Revisit if a future session adds
#: a real tokenizer for a specific provider and needs exactness over safety
#: margin.
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """A conservative (over-counting) token estimate for one string.

    Never zero for non-empty text — an empty estimate for a one-character
    string would make a truly tiny fragment look free, and the budget
    accounting elsewhere in this package assumes every admitted item costs
    at least one token.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))
