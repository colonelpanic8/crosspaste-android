#!/usr/bin/env python3
"""Append indexed APK versions to generated F-Droid metadata."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

logger.remove()

from androguard.core.apk import APK


def read_versions(repo_dir: Path, app_id: str) -> list[tuple[int, str]]:
    versions: dict[int, str] = {}
    for apk_path in sorted(repo_dir.glob("*.apk")):
        apk = APK(str(apk_path))
        package = apk.get_package()
        if package != app_id:
            raise ValueError(
                f"{apk_path.name} has package {package!r}, expected {app_id!r}"
            )
        if "android.hardware.type.watch" in apk.get_features():
            raise ValueError(
                f"{apk_path.name} is a watch APK; the phone repository accepts crosspaste-community.apk only"
            )
        code = apk.get_androidversion_code()
        name = apk.get_androidversion_name() or ""
        if code is None:
            raise ValueError(f"{apk_path.name} has no versionCode")
        numeric_code = int(code)
        if numeric_code in versions and versions[numeric_code] != name:
            raise ValueError(f"versionCode {numeric_code} has conflicting names")
        versions[numeric_code] = name
    return sorted(versions.items())


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} FDROID_ROOT APPLICATION_ID", file=sys.stderr)
        return 2
    fdroid_root = Path(sys.argv[1])
    app_id = sys.argv[2]
    metadata_file = fdroid_root / "metadata" / f"{app_id}.yml"
    if not metadata_file.exists():
        print(f"metadata file not found: {metadata_file}", file=sys.stderr)
        return 1

    try:
        versions = read_versions(fdroid_root / "repo", app_id)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if not versions:
        print("no APKs found", file=sys.stderr)
        return 1

    current_code, current_name = versions[-1]
    lines = [
        "",
        "# Generated from the APKs collected for this binary repository.",
        f"CurrentVersion: {current_name}",
        f"CurrentVersionCode: {current_code}",
        "Builds:",
    ]
    for code, name in versions:
        lines.extend((f"  - versionName: {name}", f"    versionCode: {code}"))
    with metadata_file.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(
        "  recorded versions: "
        + ", ".join(f"{name} ({code})" for code, name in versions)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
