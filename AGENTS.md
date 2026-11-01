# CrossPaste Community patch tooling

This repository builds a separately identified Android app from a pinned,
user-supplied CrossPaste 2.2.2 installation. It is a binary patch project, not
the original mobile source code. Do not claim its APK is fully open source.

- Enter the toolchain with `direnv exec .` or `nix develop .#android`.
- Run `just fmt`, `just check`, and a signed `just build VERSION` after changes.
- `scripts/build.py` owns checksums, patching, split resource merging, package
  identity, semantic version codes, and signing verification.
- Fail closed when upstream hashes or expected method bodies change. Do not
  turn matching failures into best-effort patches.
- Package ID is `com.colonelpanic.crosspaste`; label is CrossPaste Community.
- Never change the production signing key. APKs and the F-Droid index share it.
- Keystores and passwords stay in `pass`, environment variables, and GitHub
  Actions secrets. Never commit them or print their contents.
- Generated input APKs, decoded source, build files, and release outputs stay
  out of Git. Upstream APKs are not covered by the tooling's MIT license.
- Release tags are `vMAJOR.MINOR.PATCH`; version codes use
  `major * 1_000_000 + minor * 1_000 + patch`.
- The F-Droid binary repository copies the exact signed release APK. Keep
  non-free component metadata accurate; it is not the official F-Droid catalog.
- Preserve upstream protocol crypto and authentication. Patch feature policy
  and packaging only; do not weaken pairing or transport validation.

