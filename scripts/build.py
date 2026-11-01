#!/usr/bin/env python3
"""Build a signed standalone APK from the pinned CrossPaste installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OLD_ID = "com.crosspaste.android"
APP_ID = "com.colonelpanic.crosspaste"
ANDROID = "{http://schemas.android.com/apk/res/android}"
ET.register_namespace("android", ANDROID[1:-1])


def run(*args: str | Path, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def version_code(version: str) -> int:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Version must be MAJOR.MINOR.PATCH")
    major, minor, patch = map(int, version.split("."))
    code = major * 1_000_000 + minor * 1_000 + patch
    if minor > 999 or patch > 999 or not 1 <= code <= 2_100_000_000:
        raise ValueError("Version exceeds Android version-code fields")
    return code


def prepare_inputs(directory: Path, fetch: bool):
    upstream = json.loads((ROOT / "upstream.json").read_text())
    if fetch:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="crosspaste-inputs-") as temporary:
            bundle = Path(temporary) / "inputs.zip"
            url = (
                "https://github.com/colonelpanic8/crosspaste-android/releases/download/"
                f"{upstream['sourceRelease']}/{upstream['sourceAsset']}"
            )
            run("curl", "-fL", "--retry", "3", url, "-o", bundle)
            with ZipFile(bundle) as archive:
                for entry in upstream["apks"]:
                    name = entry["file"]
                    (directory / name).write_bytes(archive.read(name))
    for entry in upstream["apks"]:
        path = directory / entry["file"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(f"Upstream checksum mismatch: {path.name}")
    return upstream


def replace_method(path: Path, signature: str, body: str):
    text = path.read_text()
    pattern = rf"(?ms)^\.method {re.escape(signature)}\n.*?^\.end method$"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {signature} in {path}, got {len(matches)}"
        )
    method = f".method {signature}\n    .locals 1\n\n{body}\n.end method"
    path.write_text(re.sub(pattern, lambda _: method, text))


def patch_tree(decoded: Path, version: str) -> list[dict]:
    smali = decoded / "smali_classes2"
    true_bool = "    const/4 v0, 0x1\n    return v0"
    true_object = "    sget-object v0, Ljava/lang/Boolean;->TRUE:Ljava/lang/Boolean;\n    return-object v0"
    patches = [
        (
            "com/crosspaste/app/d.smali",
            "public final j()Z",
            true_bool,
            "feature entitlement",
        ),
        ("com/crosspaste/app/q.smali", "public final k(I)Z", true_bool, "device count"),
        (
            "com/crosspaste/app/q.smali",
            "public final l(Z)Z",
            true_bool,
            "send/receive controls",
        ),
        (
            "com/crosspaste/app/q.smali",
            "public final m(Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;",
            true_object,
            "sync rate",
        ),
        (
            "com/crosspaste/app/AndroidAppControl$isPro$1.smali",
            "public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;",
            true_object,
            "reactive feature entitlement and ad visibility",
        ),
    ]
    applied = []
    for relative, signature, body, reason in patches:
        path = smali / relative
        replace_method(path, signature, body)
        applied.append({"file": relative, "method": signature, "purpose": reason})

    text_files = [
        *decoded.glob("smali*/**/*.smali"),
        *decoded.glob("res/**/*.xml"),
        decoded / "AndroidManifest.xml",
    ]
    for path in text_files:
        original = path.read_text()
        changed = original.replace(OLD_ID, APP_ID).replace(
            OLD_ID.replace(".", "/"), APP_ID.replace(".", "/")
        )
        if changed != original:
            path.write_text(changed)

    manifest_path = decoded / "AndroidManifest.xml"
    tree = ET.parse(manifest_path)
    manifest = tree.getroot()
    manifest.set("package", APP_ID)
    for attribute in ["requiredSplitTypes", "splitTypes"]:
        manifest.attrib.pop(ANDROID + attribute, None)
    application = manifest.find("application")
    if application is None:
        raise ValueError("Application missing from upstream manifest")
    if application.get(ANDROID + "name") != "com.pairip.application.Application":
        raise ValueError("Unexpected upstream application wrapper")
    application.set(ANDROID + "name", APP_ID + ".CrossPasteApplication")
    application.set(ANDROID + "label", "CrossPaste Community")
    application.attrib.pop(ANDROID + "isSplitRequired", None)
    for child in list(application):
        name = child.get(ANDROID + "name", "")
        if child.tag == "meta-data" and (
            name.startswith(("com.android.vending.splits", "com.android.stamp."))
            or name == "com.android.vending.derived.apk.id"
        ):
            application.remove(child)
    tree.write(manifest_path, encoding="utf-8", xml_declaration=True)

    config_path = decoded / "apktool.yml"
    config = yaml.safe_load(config_path.read_text())
    config["versionInfo"] = {
        "versionCode": version_code(version),
        "versionName": version,
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    build_config = smali / "com/crosspaste/android/BuildConfig.smali"
    text = build_config.read_text()
    text, count = re.subn(
        r"(?m)^\.field public static final VERSION_CODE:I = .+$",
        f".field public static final VERSION_CODE:I = 0x{version_code(version):x}",
        text,
    )
    if count != 1:
        raise ValueError("VERSION_CODE field not found")
    text, count = re.subn(
        r'(?m)^\.field public static final VERSION_NAME:Ljava/lang/String; = ".+"$',
        f'.field public static final VERSION_NAME:Ljava/lang/String; = "{version}"',
        text,
    )
    if count != 1:
        raise ValueError("VERSION_NAME field not found")
    build_config.write_text(text)
    return applied


def signing_environment():
    names = [
        "ANDROID_KEYSTORE_FILE",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_ALIAS",
        "ANDROID_KEY_PASSWORD",
    ]
    for name in names:
        if not os.environ.get(name):
            raise ValueError(f"Release signing requires {name}")


def build(args):
    signing_environment()
    upstream = prepare_inputs(args.inputs, args.fetch_inputs)
    jar = os.environ.get("APKEDITOR_JAR")
    if not jar:
        raise ValueError("APKEDITOR_JAR is required; enter the flake's Android shell")
    args.output.mkdir(parents=True, exist_ok=True)
    work_root = args.output.parent / "build"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=work_root) as temporary:
        work = Path(temporary)
        merged = work / "merged.apk"
        # Give the merger only the exact checksummed inputs, never stray APKs.
        verified_inputs = work / "inputs"
        verified_inputs.mkdir()
        for entry in upstream["apks"]:
            shutil.copy2(args.inputs / entry["file"], verified_inputs / entry["file"])
        run(
            "java",
            "-jar",
            jar,
            "m",
            "-i",
            verified_inputs,
            "-o",
            merged,
            "-clean-meta",
            "-validate-modules",
        )
        decoded = work / "decoded"
        run("apktool", "d", "--output", decoded, merged)
        applied = patch_tree(decoded, args.version)
        unsigned = work / "unsigned.apk"
        run(
            "apktool",
            "b",
            "--aapt",
            Path(os.environ["ANDROID_HOME"]) / "build-tools/37.0.0/aapt2",
            "--output",
            unsigned,
            decoded,
        )
        aligned = work / "aligned.apk"
        run("zipalign", "-P", "16", "-f", "4", unsigned, aligned)
        artifact = args.output / "crosspaste-community.apk"
        run(
            "apksigner",
            "sign",
            "--ks",
            os.environ["ANDROID_KEYSTORE_FILE"],
            "--ks-key-alias",
            os.environ["ANDROID_KEY_ALIAS"],
            "--ks-pass",
            "env:ANDROID_KEYSTORE_PASSWORD",
            "--key-pass",
            "env:ANDROID_KEY_PASSWORD",
            "--out",
            artifact,
            aligned,
        )
        certificate = run(
            "apksigner",
            "verify",
            "--verbose",
            "--print-certs",
            artifact,
            capture_output=True,
            text=True,
        ).stdout
        run("zipalign", "-c", "-P", "16", "4", artifact)
        metadata = run(
            "aapt2", "dump", "badging", artifact, capture_output=True, text=True
        ).stdout
        if (
            f"name='{APP_ID}'" not in metadata
            or f"versionCode='{version_code(args.version)}'" not in metadata
        ):
            raise ValueError("Built APK identity or version does not match release")
        if "application-label:'CrossPaste Community'" not in metadata:
            raise ValueError("Built APK has an unexpected label")
        (args.output / "signing-certificate.txt").write_text(certificate)
        (args.output / "apk-metadata.txt").write_text(metadata)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (args.output / "SHA256SUMS").write_text(f"{digest}  {artifact.name}\n")
        (args.output / "build-report.json").write_text(
            json.dumps(
                {
                    "package": APP_ID,
                    "versionName": args.version,
                    "versionCode": version_code(args.version),
                    "upstream": upstream,
                    "patches": applied,
                    "sha256": digest,
                },
                indent=2,
            )
            + "\n"
        )
        print(certificate)
        print(f"Built {artifact} ({digest})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path.cwd() / "inputs")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "dist")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--fetch-inputs", action="store_true")
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    version_code(args.version)
    if args.check_inputs:
        upstream = prepare_inputs(args.inputs, args.fetch_inputs)
        print(f"Verified {len(upstream['apks'])} upstream APK checksums")
    else:
        build(args)


if __name__ == "__main__":
    main()
