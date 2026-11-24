"""Shrink-only baselines: frozen violation counts per key (a file, a lint top) that
a change may lower but never raise. Shared by the comment lint and the RTL lint.

Spec: wiki/tools/lint/spec.md.
"""
from __future__ import annotations


def count(keys: list[str]) -> dict[str, int]:
    """Violations per key, sorted by key."""
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def check(counts: dict[str, int], baseline: dict[str, int], keys: list[str], noun: str, fix: str,
          update: str, details: dict[str, list[str]] | None = None) -> list[str]:
    """Error lines: keys over their baseline (with their details), and keys below
    it, whose baseline entry is stale until `update` is run."""
    errors: list[str] = []
    for key in keys:
        have, allowed = counts.get(key, 0), baseline.get(key, 0)
        if have > allowed:
            errors.append(f"{key}: {have} {noun}, baseline allows {allowed}; {fix}")
            errors.extend((details or {}).get(key, []))
        elif have < allowed:
            errors.append(f"{key}: {have} {noun}, below its baseline of {allowed}; run {update} and commit it")
    return errors


def raised(baseline: dict[str, int], base: dict[str, int], noun: str, clean: str,
           new: str = "new files") -> list[str]:
    """Error lines for a baseline that grew against the base branch's: any entry
    above its base value, a higher total, or new entries holding more than the
    entries that disappeared (a rename moves counts; a new key brings none)."""
    errors = [f"{key}: baseline raised from {base[key]} to {n}; {clean} instead"
              for key, n in sorted(baseline.items()) if key in base and n > base[key]]
    if sum(baseline.values()) > sum(base.values()):
        errors.append(f"baseline total raised from {sum(base.values())} to {sum(baseline.values())}; "
                      f"{new} must have no {noun}")
    added = sum(n for key, n in baseline.items() if key not in base)
    moved = sum(n for key, n in base.items() if key not in baseline)
    if added > moved:
        errors.append(f"new baseline entries hold {added} violations but only {moved} moved from removed "
                      f"entries; {new} must have no {noun}")
    return errors
