"""`A2` §7's gate-specific instruction table, verbatim — the `{gate}` /
`{gate_specific_instruction}` placeholders `repair/v1.md` expects.

No caller exists yet (the repair loop is T9.1, Phase 11) — kept here as
data because `A2` gives it as a fixed table, not because anything in this
ticket's scope reads it. `06`'s own note on G5 ("when a fix doesn't fix,
patching harder is futile") is the one instruction worth remembering
before anyone is tempted to build a generic fallback for a gate not
listed here: there isn't one, by design — an unrecognised gate is a bug
in the caller, not a case this table should grow a default for."""

from __future__ import annotations

GATE_INSTRUCTIONS: dict[str, str] = {
    "G1": (
        "Fix only the syntax error. The parser output identifies the exact location. "
        "Change nothing else."
    ),
    "G2": (
        "Your patch imports a package unavailable in the validation environment. Use "
        "only modules already imported in the retrieved files or present in the "
        "manifest."
    ),
    "G3": "Type or import error. The compiler output is verbatim below. Fix precisely that.",
    "G4": (
        "Your regression test PASSED on unpatched code, so it does not reproduce the "
        "bug. Regenerate ONLY the test. The fix may well be correct — leave it "
        "unchanged. The test must fail with the reported exception family on the "
        "original code."
    ),
    "G5": (
        "Your fix did not resolve the error — the test still fails after the patch. "
        "**The diagnosis is likely wrong.** Reconsider the root cause from the "
        "evidence before writing more code."
    ),
    "G6": (
        "Your patch broke tests that previously passed. Each is listed with its "
        "failure. Either preserve the existing contract, or update those tests and "
        "justify why the behaviour change is correct."
    ),
    "G7": (
        "New static-analysis findings listed below. Remediate exactly those. Do not "
        "refactor unrelated code."
    ),
    "G8": (
        "Your patch introduced a dangerous construct, listed below. Remove it and "
        "achieve the fix safely."
    ),
}
