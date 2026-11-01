set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

fmt:
    alejandra flake.nix
    ruff format scripts

fmt-check:
    alejandra --check flake.nix
    ruff format --check scripts

lint:
    ruff check scripts
    shellcheck scripts/fdroid/build-repo.sh

check: fmt-check lint
    python3 scripts/fdroid/changelogs.py --check
    python3 scripts/build.py --check-inputs
    python3 scripts/build.py --check-helper

build version="1.0.0":
    python3 scripts/build.py --version {{version}}

release version="1.0.0": (build version)

fdroid-repo:
    ./scripts/fdroid/build-repo.sh

install device="100.92.9.91:5555":
    adb -s {{device}} install -r dist/crosspaste-community.apk

