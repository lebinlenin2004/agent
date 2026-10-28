"""Helpers for treating web content as untrusted data."""
import re

_TAG = re.compile(r"<[^>]*>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DELIM = re.compile(r"</?\s*untrusted[_a-z]*\s*>", re.I)

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|messages)",
    r"disregard .{0,40}(instructions|rules|guidelines)",
    r"\byou are now\b",
    r"(reveal|print|show) .{0,30}(system prompt|api key|secret|credentials)",
    r"new (system )?instructions",
    r"mark (this|the) (job|vacancy|posting|role) as (active|verified|open)",
    r"(set|give) (the )?(match )?score (to )?(100|maximum)",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def clean_field(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    value = _CTRL.sub("", _TAG.sub(" ", str(value)))
    value = " ".join(value.split())[:max_len]
    return value or None


def detect_injection(text: str) -> list[str]:
    """Informational only: flags suspicious instructions in page content for the evidence trail."""
    return [p.pattern for p in _INJ if p.search(text or "")]


def neutralize(text: str) -> str:
    """Stop page text from closing our delimiter tags and escaping the data block."""
    return _DELIM.sub(" ", text)
