#!/usr/bin/env python3
"""Generate fastlane changelogs from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
OUT_DIR = REPO_ROOT / "fastlane/metadata/android/en-US/changelogs"
MAX_CHARS = 500
RELEASE_HEADING = re.compile(r"^##\s+\[(\d+)\.(\d+)\.(\d+)\]")
SECTION_HEADING = re.compile(r"^###\s+(.+?)\s*$")


def version_code(version: tuple[int, int, int]) -> int:
    major, minor, patch = version
    if minor > 999 or patch > 999:
        raise ValueError(f"version {major}.{minor}.{patch} exceeds version-code fields")
    code = major * 1_000_000 + minor * 1_000 + patch
    if not 1 <= code <= 2_100_000_000:
        raise ValueError(f"version {major}.{minor}.{patch} has invalid Android code {code}")
    return code


MORE = "* ...see CHANGELOG.md for the rest."


def render(entries: list[str]) -> str:
    if not entries:
        return "Maintenance release.\n"

    lines: list[str] = []
    used = 0
    for index, entry in enumerate(entries):
        candidate = f"* {entry}"
        trailer = 0 if index == len(entries) - 1 else len(MORE) + 1
        room = MAX_CHARS - used - trailer - 1
        if len(candidate) > room:
            if not lines:
                lines.append(candidate[: MAX_CHARS - len(MORE) - 3].rstrip() + "…")
            lines.append(MORE)
            break
        lines.append(candidate)
        used += len(candidate) + 1
    return "\n".join(lines) + "\n"


def expected_changelogs(text: str) -> dict[int, str]:
    expected: dict[int, str] = {}
    version: tuple[int, int, int] | None = None
    section: str | None = None
    entries: list[str] = []

    def flush() -> None:
        if version is not None:
            code = version_code(version)
            if code in expected:
                raise ValueError(f"duplicate Android versionCode {code}")
            expected[code] = render(entries)

    for line in text.splitlines():
        heading = RELEASE_HEADING.match(line)
        if heading:
            flush()
            version = tuple(int(part) for part in heading.groups())
            section = None
            entries = []
            continue

        if version is None:
            continue

        section_match = SECTION_HEADING.match(line)
        if section_match:
            section = section_match.group(1)
        elif line.startswith("- "):
            entry = line[2:].strip()
            entries.append(f"{section}: {entry}" if section else entry)
        elif entries and line.startswith("  ") and line.strip():
            entries[-1] = f"{entries[-1]} {line.strip()}"

    flush()
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    try:
        expected = expected_changelogs(CHANGELOG.read_text(encoding="utf-8"))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if not expected:
        print("no semantic-version releases found in CHANGELOG.md", file=sys.stderr)
        return 1

    stale: list[Path] = []
    for code, body in sorted(expected.items()):
        target = OUT_DIR / f"{code}.txt"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == body:
            continue
        if args.check:
            stale.append(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    expected_names = {f"{code}.txt" for code in expected}
    for orphan in sorted(OUT_DIR.glob("*.txt")) if OUT_DIR.exists() else []:
        if orphan.name not in expected_names:
            if args.check:
                stale.append(orphan)
            else:
                orphan.unlink()

    if stale:
        print("fastlane changelogs are out of date:", file=sys.stderr)
        for path in stale:
            print(f"  {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    if not args.check:
        print(f"wrote {len(expected)} changelog(s) to {OUT_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
