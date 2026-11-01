#!/usr/bin/env python3
"""Run a command with the production signing identity from pass."""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: with-signing.py COMMAND [ARGUMENT ...]")
    record = json.loads(
        subprocess.check_output(["pass", "show", "android-signing/crosspaste-community"], text=True)
    )
    with tempfile.TemporaryDirectory(prefix="crosspaste-signing-") as temporary:
        keystore = Path(temporary) / "release.jks"
        keystore.write_bytes(base64.b64decode(record["ANDROID_KEYSTORE_BASE64"], validate=True))
        keystore.chmod(0o600)
        env = os.environ | {
            key: value for key, value in record.items() if key != "ANDROID_KEYSTORE_BASE64"
        }
        env["ANDROID_KEYSTORE_FILE"] = str(keystore)
        result = subprocess.run(sys.argv[1:], env=env, check=False)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
