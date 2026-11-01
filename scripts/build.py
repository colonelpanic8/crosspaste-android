#!/usr/bin/env python3
"""Build a signed standalone APK from the pinned CrossPaste installation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OLD_ID = "com.crosspaste.android"
APP_ID = "com.colonelpanic.crosspaste"
ANDROID = "{http://schemas.android.com/apk/res/android}"
FLEET_URL = "https://raw.githubusercontent.com/colonelpanic8/dotfiles/master/nix-shared/crosspaste-fleet.json"
FLEET_ASSET = "crosspaste-fleet.json"
HELPER_SOURCES = ROOT / "helper"
HELPER_CLASS = "Lcom/colonelpanic/crosspaste/fleet/FleetTrust;"
ANDROID_PLATFORM = "android-37.0"
MIN_SDK = 32
UPSTREAM_DEX_COUNT = 5
FLEET_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
FLEET_HOST = re.compile(r"[A-Za-z0-9.:%-]{1,253}")
P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420004")
P256_P = 2**256 - 2**224 + 2**192 + 2**96 - 1
P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
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


def require_int(value, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"Fleet field {name} must be an integer in [{low}, {high}]")
    return value


def require_text(value, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Fleet field {name} must be a non-empty string")
    return value


def validate_fleet(data: bytes) -> list[str]:
    """Mirror the helper's validation so a bad list fails the build, not the device."""
    fleet = json.loads(data.decode("utf-8"))
    if not isinstance(fleet, dict) or require_int(fleet.get("version"), "version", 1, 1) != 1:
        raise ValueError("Unsupported fleet version")
    members = fleet.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("Fleet has no members")
    names, ids = [], set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Fleet member must be an object")
        name = require_text(member.get("name"), "name")
        member_id = require_text(member.get("appInstanceId"), f"{name}.appInstanceId")
        if not FLEET_ID.fullmatch(member_id) or member_id in ids:
            raise ValueError(f"Invalid or duplicate appInstanceId for {name}")
        ids.add(member_id)
        platform = member.get("platform")
        if not isinstance(platform, dict):
            raise ValueError(f"Fleet member {name} has no platform")
        for field in ["name", "arch", "version"]:
            require_text(platform.get(field), f"{name}.platform.{field}")
        require_int(platform.get("bitMode"), f"{name}.platform.bitMode", 1, 128)
        key = base64.b64decode(
            require_text(member.get("cryptPublicKey"), f"{name}.cryptPublicKey"), validate=True
        )
        if len(key) != 91 or not key.startswith(P256_SPKI_PREFIX):
            raise ValueError(f"Fleet key for {name} is not an uncompressed P-256 SPKI key")
        x = int.from_bytes(key[27:59], "big")
        y = int.from_bytes(key[59:91], "big")
        if x >= P256_P or y >= P256_P or (y * y - (x**3 - 3 * x + P256_B)) % P256_P:
            raise ValueError(f"Fleet key for {name} is not a point on P-256")
        hosts = member.get("hostAddresses")
        if not isinstance(hosts, list) or not hosts:
            raise ValueError(f"Fleet member {name} has no hostAddresses")
        for host in hosts:
            if not isinstance(host, str) or not FLEET_HOST.fullmatch(host):
                raise ValueError(f"Invalid host address for {name}")
        require_int(member.get("port"), f"{name}.port", 1, 65535)
        names.append(name)
    return names


def load_fleet(source: str) -> bytes:
    if source.startswith("https://"):
        data = run(
            "curl", "-fsSL", "--retry", "3", "--max-time", "60", source, capture_output=True
        ).stdout
    elif "://" in source:
        raise ValueError("Fleet URL must use HTTPS")
    else:
        data = Path(source).read_bytes()
    validate_fleet(data)
    return data


def compile_helper(work: Path) -> Path:
    android_jar = Path(os.environ["ANDROID_HOME"]) / "platforms" / ANDROID_PLATFORM / "android.jar"
    if not android_jar.is_file():
        raise ValueError(f"Missing {android_jar}")
    sources = sorted(HELPER_SOURCES.rglob("*.java"))
    if not sources:
        raise ValueError("Fleet helper sources are missing")
    classes = work / "helper-classes"
    dex = work / "helper-dex"
    classes.mkdir()
    dex.mkdir()
    run(
        "javac",
        "-source",
        "8",
        "-target",
        "8",
        "-bootclasspath",
        android_jar,
        "-Xlint:all,-options",
        "-Werror",
        "-encoding",
        "UTF-8",
        "-d",
        classes,
        *sources,
    )
    run(
        "d8",
        "--release",
        "--min-api",
        str(MIN_SDK),
        "--lib",
        android_jar,
        "--output",
        dex,
        *sorted(classes.rglob("*.class")),
    )
    outputs = sorted(path.name for path in dex.iterdir())
    if outputs != ["classes.dex"]:
        raise ValueError(f"Unexpected helper dex output: {outputs}")
    return dex / "classes.dex"


def hook_application(decoded: Path) -> dict:
    # The namespace rename rewrites class names, not smali file paths.
    relative = f"{OLD_ID.replace('.', '/')}/CrossPasteApplication.smali"
    path = decoded / "smali_classes2" / relative
    text = path.read_text()
    if f".class public L{APP_ID.replace('.', '/')}/CrossPasteApplication;" not in text:
        raise ValueError("CrossPasteApplication was not renamed")
    pattern = (
        r"(?m)^(\.method public final onCreate\(\)V\n    \.locals 11\n)"
        r"(?=\n(?:    \.line 1\n)?    sget-object v1, Ll82;->a:Lln3;\n)"
    )
    call = f"    invoke-static {{p0}}, {HELPER_CLASS}->start(Landroid/content/Context;)V\n"
    text, count = re.subn(pattern, lambda match: match.group(1) + "\n" + call, text)
    if count != 1:
        raise ValueError(f"Expected one known CrossPasteApplication.onCreate body, got {count}")
    path.write_text(text)
    return {"file": relative, "method": "public final onCreate()V", "purpose": "fleet trust"}


def add_helper_dex(apk: Path, helper: Path) -> str:
    with ZipFile(apk) as archive:
        dex = sorted(name for name in archive.namelist() if re.fullmatch(r"classes\d*\.dex", name))
    expected = sorted(
        ["classes.dex", *(f"classes{index}.dex" for index in range(2, UPSTREAM_DEX_COUNT + 1))]
    )
    if dex != expected:
        raise ValueError(f"Unexpected dex files in rebuilt APK: {dex}")
    name = f"classes{UPSTREAM_DEX_COUNT + 1}.dex"
    with ZipFile(apk, "a", compression=ZIP_DEFLATED) as archive:
        archive.write(helper, name)
    return name


def replace_method(path: Path, signature: str, body: str):
    text = path.read_text()
    pattern = rf"(?ms)^\.method {re.escape(signature)}\n.*?^\.end method$"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {signature} in {path}, got {len(matches)}")
    method = f".method {signature}\n    .locals 1\n\n{body}\n.end method"
    path.write_text(re.sub(pattern, lambda _: method, text))


def patch_tree(decoded: Path, version: str, fleet: bytes) -> list[dict]:
    smali = decoded / "smali_classes2"
    true_bool = "    const/4 v0, 0x1\n    return v0"
    true_object = (
        "    sget-object v0, Ljava/lang/Boolean;->TRUE:Ljava/lang/Boolean;\n    return-object v0"
    )
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
    applied.append(hook_application(decoded))
    asset = decoded / "assets" / FLEET_ASSET
    if asset.exists():
        raise ValueError(f"Upstream already ships {asset.name}")
    asset.write_bytes(fleet)

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
    fleet = load_fleet(args.fleet)
    fleet_members = validate_fleet(fleet)
    jar = os.environ.get("APKEDITOR_JAR")
    if not jar:
        raise ValueError("APKEDITOR_JAR is required; enter the flake's Android shell")
    args.output.mkdir(parents=True, exist_ok=True)
    work_root = args.output.parent / "build"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=work_root) as temporary:
        work = Path(temporary)
        helper = compile_helper(work)
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
        applied = patch_tree(decoded, args.version, fleet)
        unsigned = work / "unsigned.apk"
        aapt = work / "aapt2"
        shutil.copy2(Path(os.environ["ANDROID_HOME"]) / "build-tools/37.0.0/aapt2", aapt)
        aapt.chmod(0o700)
        run(
            "apktool",
            "b",
            "--aapt",
            aapt,
            "--output",
            unsigned,
            decoded,
        )
        helper_dex = add_helper_dex(unsigned, helper)
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
        with ZipFile(artifact) as archive:
            if archive.read(helper_dex) != helper.read_bytes():
                raise ValueError("Signed APK is missing the fleet helper dex")
            if archive.read(f"assets/{FLEET_ASSET}") != fleet:
                raise ValueError("Signed APK is missing the bundled fleet list")
        metadata = run("aapt2", "dump", "badging", artifact, capture_output=True, text=True).stdout
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
                    "fleet": {
                        "source": args.fleet,
                        "sha256": hashlib.sha256(fleet).hexdigest(),
                        "members": fleet_members,
                    },
                    "helper": {
                        "dex": helper_dex,
                        "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                    },
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
    parser.add_argument("--check-helper", action="store_true")
    parser.add_argument("--fleet", default=FLEET_URL, help="Fleet JSON path or HTTPS URL")
    args = parser.parse_args()
    version_code(args.version)
    if args.check_helper:
        with tempfile.TemporaryDirectory(prefix="crosspaste-helper-") as temporary:
            helper = compile_helper(Path(temporary))
            print(f"Compiled fleet helper ({hashlib.sha256(helper.read_bytes()).hexdigest()})")
    elif args.check_inputs:
        upstream = prepare_inputs(args.inputs, args.fetch_inputs)
        print(f"Verified {len(upstream['apks'])} upstream APK checksums")
    else:
        build(args)


if __name__ == "__main__":
    main()
