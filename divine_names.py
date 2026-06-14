#!/usr/bin/env python3
"""Replace Hebrew Divine names with non-sacred placeholders.

The regexes mirror the Apps Script replacement behavior, but use a narrower
Hebrew-mark range so punctuation such as maqaf and sof pasuq is preserved.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass


DEFAULT_TETRAGRAMMATON_REPLACEMENT = "יקוק"
DEFAULT_YAH_REPLACEMENT = "קה"
DEFAULT_ELOHIM_REPLACEMENT = "אלקים"
DEFAULT_ELOHIM_STEM_REPLACEMENT = "אלק"
FALSE_VALUES = {"0", "false", "no", "off"}

HEBREW_MARK_CHARS = "\u0591-\u05bd\u05bf-\u05c2\u05c4-\u05c5\u05c7"
HEBREW_LETTER_CHARS = "\u05d0-\u05ea"
HEBREW_MARK_RE = re.compile(f"[{HEBREW_MARK_CHARS}]")
HEBREW_WORD_CHAR = f"[{HEBREW_MARK_CHARS}{HEBREW_LETTER_CHARS}]"
HEBREW_MARKS = f"[{HEBREW_MARK_CHARS}]*"


@dataclass(frozen=True)
class DivineNameReplacements:
    tetragrammaton: str = DEFAULT_TETRAGRAMMATON_REPLACEMENT
    yah: str = DEFAULT_YAH_REPLACEMENT
    elohim: str = DEFAULT_ELOHIM_REPLACEMENT
    elohim_stem: str = DEFAULT_ELOHIM_STEM_REPLACEMENT

    @classmethod
    def from_environment(cls) -> "DivineNameReplacements":
        return cls(
            tetragrammaton=os.environ.get(
                "PARASHA_TETRAGRAMMATON_REPLACEMENT",
                os.environ.get(
                    "PARASHA_MEFORASH_REPLACEMENT",
                    DEFAULT_TETRAGRAMMATON_REPLACEMENT,
                ),
            ),
            yah=os.environ.get("PARASHA_YAH_REPLACEMENT", DEFAULT_YAH_REPLACEMENT),
            elohim=os.environ.get(
                "PARASHA_ELOHIM_REPLACEMENT",
                DEFAULT_ELOHIM_REPLACEMENT,
            ),
            elohim_stem=os.environ.get(
                "PARASHA_ELOHIM_STEM_REPLACEMENT",
                DEFAULT_ELOHIM_STEM_REPLACEMENT,
            ),
        )


def marked_letter(letter: str) -> str:
    return f"{re.escape(letter)}{HEBREW_MARKS}"


def marked_word(letters: str) -> str:
    return "".join(marked_letter(letter) for letter in letters)


TETRAGRAMMATON_RE = re.compile(marked_word("יהוה"))
YAH_RE = re.compile(
    fr"(?<!{HEBREW_WORD_CHAR}){marked_word('יה')}(?!{HEBREW_WORD_CHAR})"
)

ELOHIM_SUFFIXES = (
    "יכם",
    "יכן",
    "יהם",
    "יהן",
    "ינו",
    "יך",
    "יו",
    "יה",
    "ים",
    "י",
)
ELOHIM_SUFFIX_RE = "|".join(marked_word(suffix) for suffix in ELOHIM_SUFFIXES)
ELOHIM_FAMILY_RE = re.compile(
    marked_word("אל")
    + f"(?:{marked_letter('ו')})?"
    + marked_letter("ה")
    + f"(?P<suffix>{ELOHIM_SUFFIX_RE})"
)


def divine_name_replacement_enabled() -> bool:
    value = os.environ.get("PARASHA_REPLACE_DIVINE_NAMES", "1").strip().lower()
    return value not in FALSE_VALUES


def strip_hebrew_marks(text: str) -> str:
    return HEBREW_MARK_RE.sub("", text)


def replace_elohim_family(match: re.Match[str], replacements: DivineNameReplacements) -> str:
    suffix = strip_hebrew_marks(match.group("suffix"))
    if suffix == "ים":
        return replacements.elohim
    return f"{replacements.elohim_stem}{suffix}"


def replace_divine_names(
    text: str,
    replacements: DivineNameReplacements | None = None,
    enabled: bool | None = None,
) -> str:
    if enabled is None:
        enabled = divine_name_replacement_enabled()
    if not enabled:
        return text

    replacements = replacements or DivineNameReplacements.from_environment()
    text = TETRAGRAMMATON_RE.sub(replacements.tetragrammaton, text)
    text = YAH_RE.sub(replacements.yah, text)
    return ELOHIM_FAMILY_RE.sub(
        lambda match: replace_elohim_family(match, replacements),
        text,
    )


def main() -> None:
    sys.stdout.write(replace_divine_names(sys.stdin.read()))


if __name__ == "__main__":
    main()
