# CrossPaste Community for Android

CrossPaste Android 2.2.2 with its local premium feature restrictions removed,
packaged as **CrossPaste Community** (`com.colonelpanic.crosspaste`). Install it
alongside the Google Play app; it has separate settings, history, and pairings.

- [Download the latest signed APK](https://github.com/colonelpanic8/crosspaste-android/releases/latest)
- [Add to Obtainium](obtainium://add/https://github.com/colonelpanic8/crosspaste-android)
- [F-Droid repository and signing fingerprint](https://colonelpanic8.github.io/crosspaste-android/)

The same signing identity is used for GitHub release APKs and the self-hosted
F-Droid index. Updates from either source are interchangeable. This repository
is not an official CrossPaste release or a listing in F-Droid's official catalog.

## Changes

The patch enables the synchronous and reactive feature-entitlement paths and
removes device-count, sync-rate, and per-device send/receive restrictions.
That also enables the original premium-gated clipboard, widget, drag, and floating
overlay features, and suppresses ad placements that follow the entitlement flow.
Original third-party SDKs remain bundled.

Split resources are merged into one standalone APK. The package namespace and
provider authorities are renamed, the Play startup licensing wrapper is replaced
by the original application class, and release versions are derived from semver.
Pairing, encryption, and transport authentication are left intact.

## Fleet trust

A small helper (`helper/`, compiled with `javac` and `d8` into an extra dex)
starts from the application's `onCreate`. It reads the public fleet list from
[`nix-shared/crosspaste-fleet.json`](https://github.com/colonelpanic8/dotfiles/blob/master/nix-shared/crosspaste-fleet.json)
and seeds the same trust rows PIN pairing would create: each peer's public key
in `CryptPublicKey` and a `SyncRuntimeInfoEntity` row with its addresses. It
skips the member whose `appInstanceId` equals this device's app-scoped
`ANDROID_ID`, and does nothing if no member matches. Existing rows keep their
state; only missing fleet addresses are added. Rows outside the fleet are never
removed.

The list is applied from the cache in `files/crosspaste-fleet.json`, or the copy
bundled at build time, before the app opens its database. A background thread
fetches the URL every 15 minutes and re-applies the cache every minute. Writes
use the app's bundled requery SQLite. Logs use the `CrossPasteFleet` tag. The
build validates the list and accepts `--fleet PATH_OR_HTTPS_URL`.

## Build

The toolchain follows the Nix flake + direnv + just layout. `flake.lock` pins
Nixpkgs and the Android SDK; APKEditor 1.4.9 is checksum-pinned in `flake.nix`.

```sh
direnv allow
direnv exec . python3 scripts/build.py --check-inputs --fetch-inputs
direnv exec . just check
```

The upstream input bundle is kept in the `upstream-2.2.2` prerelease. It contains
the four APKs extracted from the Google Play installation; `upstream.json` pins
each checksum. You can instead put the matching APKs under `inputs/` yourself.
The build stops on a checksum or expected-method mismatch. Updating upstream
requires reviewing the new APK rather than applying these offsets blindly.

For a signed build, provide these environment variables:

```text
ANDROID_KEYSTORE_FILE
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_ALIAS
ANDROID_KEY_PASSWORD
```

Then run `direnv exec . just build 1.0.0`. Output is
`dist/crosspaste-community.apk`, with certificate, metadata, checksum, and a
machine-readable build report beside it. No debug signing fallback exists.
`nix run . -- --version 1.0.0` exposes the same builder.

## Publishing

1. Update `CHANGELOG.md`, run `just fmt` and `just check`, and commit.
2. Create and push `vMAJOR.MINOR.PATCH`.
3. The Release workflow builds and verifies a signed APK and attaches it to the
   GitHub release. It reads the four `ANDROID_*` signing secrets documented below.
4. The F-Droid workflow downloads a short release history, verifies a consistent
   signer, signs the index, and publishes GitHub Pages.

Actions secrets: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS`, and `ANDROID_KEY_PASSWORD`. The production keystore is
backed up in `pass` under `android-signing/crosspaste-community`. Pages uses
the GitHub Actions source.

For a local index build, enter the flake shell, set the signing environment above,
and run `just fdroid-repo`. `FDROID_APK_FILE` can select a local signed APK.
The collector stops at a signer boundary and never mixes debug and production
signing identities.

## Scope and licensing

This is a binary patch project, not a recovery or publication of CrossPaste's
complete Android Kotlin source. Some upstream methods do not decompile cleanly;
the build operates on bytecode. The MIT license applies only to original patch
and publishing tooling in this repository. CrossPaste and bundled dependencies
retain their original copyrights and licensing terms.

Android still requires the appropriate device permissions for notifications,
overlays, keyboard, and accessibility features. The accessibility clipboard
workaround briefly focuses an overlay; removing feature gates does not remove
Android platform restrictions.
