"""Plain-text script parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass


_LINE_RE = re.compile(r"^\s*(?P<speaker>[A-Za-z][A-Za-z0-9 _-]{0,48})\s*:\s*(?P<text>.+?)\s*$")


class ScriptParseError(ValueError):
    """Raised when a plain-text script is malformed."""


@dataclass(frozen=True)
class ParsedLine:
    speaker: str | None
    text: str
    line_number: int


@dataclass(frozen=True)
class ParsedScript:
    lines: tuple[ParsedLine, ...]
    speaker_names: tuple[str, ...]
    has_unlabeled_lines: bool

    @property
    def is_ambiguous(self) -> bool:
        return self.has_unlabeled_lines or not self.speaker_names


def parse_text(text: str) -> ParsedScript:
    if not text or not text.strip():
        raise ScriptParseError("script is empty")
    lines: list[ParsedLine] = []
    names: list[str] = []
    unlabeled = False
    for number, raw in enumerate(text.splitlines(), start=1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        match = _LINE_RE.match(raw)
        if match:
            speaker = match.group("speaker").strip()
            line_text = match.group("text").strip()
            if speaker not in names:
                names.append(speaker)
            lines.append(ParsedLine(speaker, line_text, number))
        else:
            unlabeled = True
            lines.append(ParsedLine(None, value, number))
    if not lines:
        raise ScriptParseError("script has no dialogue lines")
    return ParsedScript(tuple(lines), tuple(names), unlabeled)


def assign_unlabeled(script: ParsedScript, mode: str) -> ParsedScript:
    """Resolve unlabeled input as one narrator or alternating generated speakers."""
    if not script.is_ambiguous:
        return script
    if mode not in {"narrator", "alternate"}:
        raise ScriptParseError("ambiguous script requires mode 'narrator' or 'alternate'")
    names = list(script.speaker_names)
    if mode == "narrator":
        names = [names[0] if names else "Narrator"]
    elif not names:
        names = ["Speaker 1", "Speaker 2"]
    elif len(names) == 1:
        names.append("Speaker 2")
    assigned: list[ParsedLine] = []
    next_index = 0
    for line in script.lines:
        if line.speaker is not None:
            assigned.append(line)
            continue
        speaker = names[0] if mode == "narrator" else names[next_index % 2]
        next_index += 1
        assigned.append(ParsedLine(speaker, line.text, line.line_number))
    return ParsedScript(tuple(assigned), tuple(names), False)
