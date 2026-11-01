#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

out_dir="${FDROID_OUT_DIR:-target/fdroid}"
release_count="${FDROID_RELEASE_COUNT:-4}"
github_repository="${FDROID_GITHUB_REPOSITORY:-colonelpanic8/crosspaste-android}"
app_id="com.colonelpanic.crosspaste"

if [[ -z "$out_dir" || "$out_dir" == "/" || "$out_dir" == "." || "$out_dir" == ".." || "$out_dir" == "$repo_root" ]]; then
  echo "Refusing unsafe FDROID_OUT_DIR: $out_dir" >&2
  exit 1
fi
if [[ ! "$release_count" =~ ^[1-9][0-9]*$ ]]; then
  echo "FDROID_RELEASE_COUNT must be a positive integer" >&2
  exit 1
fi

: "${FDROID_KEYSTORE_BASE64:=${ANDROID_KEYSTORE_BASE64:-}}"
: "${FDROID_KEYSTORE_FILE:=${ANDROID_KEYSTORE_FILE:-}}"
: "${FDROID_KEY_ALIAS:=${ANDROID_KEY_ALIAS:-}}"
: "${FDROID_KEYSTORE_PASSWORD:=${ANDROID_KEYSTORE_PASSWORD:-}}"
: "${FDROID_KEY_PASSWORD:=${ANDROID_KEY_PASSWORD:-}}"
export FDROID_KEY_ALIAS FDROID_KEYSTORE_PASSWORD FDROID_KEY_PASSWORD

if [[ -z "$FDROID_KEYSTORE_BASE64" && -z "$FDROID_KEYSTORE_FILE" ]]; then
  echo "Set FDROID_KEYSTORE_BASE64 or FDROID_KEYSTORE_FILE" >&2
  exit 1
fi
for variable in FDROID_KEY_ALIAS FDROID_KEYSTORE_PASSWORD FDROID_KEY_PASSWORD; do
  if [[ -z "${!variable}" ]]; then
    echo "Signing the repository index requires $variable" >&2
    exit 1
  fi
done
for tool in curl fdroid gh python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required tool not found: $tool" >&2; exit 1; }
done

if command -v apksigner >/dev/null 2>&1; then
  apksigner_bin="$(command -v apksigner)"
else
  shopt -s nullglob
  apksigners=("${ANDROID_HOME:-/nonexistent}"/build-tools/*/apksigner)
  shopt -u nullglob
  if ((${#apksigners[@]} == 0)); then
    echo "apksigner not found; enter the Android dev shell" >&2
    exit 1
  fi
  apksigner_bin="${apksigners[-1]}"
fi

python3 scripts/fdroid/changelogs.py
rm -rf -- "$out_dir"
mkdir -p "$out_dir/repo" "$out_dir/metadata/$app_id/en-US"
cp fdroid/config.yml "$out_dir/config.yml"
cp fdroid/metadata/*.yml "$out_dir/metadata/"
cp -r fastlane/metadata/android/en-US/. "$out_dir/metadata/$app_id/en-US/"
cp fdroid/icon.png "$out_dir/icon.png"

download_dir="$(mktemp -d)"
keystore="$out_dir/keystore.jks"
cleanup() {
  rm -rf -- "$download_dir"
  rm -f -- "$keystore"
}
trap cleanup EXIT

apk_count=0
apk_signer=""
if [[ -n "${FDROID_APK_FILE:-}" ]]; then
  if [[ ! -f "$FDROID_APK_FILE" ]]; then
    echo "FDROID_APK_FILE does not exist: $FDROID_APK_FILE" >&2
    exit 1
  fi
  source_apks=("$FDROID_APK_FILE")
  source_labels=("local")
else
  mapfile -t tags < <(
    gh release list --repo "$github_repository" --limit "$release_count" \
      --json tagName,isDraft,isPrerelease \
      --jq '.[] | select(.isDraft == false and .isPrerelease == false) | .tagName'
  )
  if ((${#tags[@]} == 0)); then
    echo "No published GitHub releases found for $github_repository" >&2
    exit 1
  fi
  source_apks=()
  source_labels=()
  for tag in "${tags[@]}"; do
    tag_dir="$download_dir/$tag"
    mkdir -p "$tag_dir"
    if curl -fsSL --retry 3 --retry-delay 2 \
      "https://github.com/$github_repository/releases/download/$tag/crosspaste-community.apk" \
      -o "$tag_dir/crosspaste-community.apk"; then
      source_apks+=("$tag_dir/crosspaste-community.apk")
      source_labels+=("$tag")
    else
      echo "  $tag: no crosspaste-community.apk asset, skipping"
    fi
  done
fi

for index in "${!source_apks[@]}"; do
  source_apk="${source_apks[$index]}"
  label="${source_labels[$index]}"
  if ! cert_output="$("$apksigner_bin" verify --print-certs "$source_apk" 2>&1)"; then
    echo "$label: APK signature verification failed" >&2
    printf '%s\n' "$cert_output" >&2
    exit 1
  fi
  signer="$(printf '%s\n' "$cert_output" | sed -nE 's/^.*certificate SHA-256 digest:[[:space:]]*//p' | head -1)"
  if [[ -z "$signer" ]]; then
    echo "$label: could not read the APK signing certificate" >&2
    exit 1
  fi
  if [[ -z "$apk_signer" ]]; then
    apk_signer="$signer"
  elif [[ "$signer" != "$apk_signer" ]]; then
    echo "  $label: signer differs from newest release; stopping at key boundary"
    break
  fi
  destination="$out_dir/repo/crosspaste-community-${label#v}.apk"
  cp "$source_apk" "$destination"
  apk_count=$((apk_count + 1))
done
if ((apk_count == 0)); then
  echo "No coherent signed APK history was collected" >&2
  exit 1
fi

python3 scripts/fdroid/apply-versions.py "$out_dir" "$app_id"
if [[ -n "$FDROID_KEYSTORE_FILE" ]]; then
  cp "$FDROID_KEYSTORE_FILE" "$keystore"
else
  printf '%s' "$FDROID_KEYSTORE_BASE64" | base64 -d > "$keystore"
fi
chmod 600 "$keystore"
(
  cd "$out_dir"
  fdroid update --pretty --verbose
)

fingerprint="$(
  keytool -list -v -keystore "$keystore" -alias "$FDROID_KEY_ALIAS" \
    -storepass "$FDROID_KEYSTORE_PASSWORD" 2>/dev/null \
    | sed -n 's/^[[:space:]]*SHA256:[[:space:]]*//p' \
    | head -1 | tr -d ':' | tr '[:upper:]' '[:lower:]'
)"
rm -f -- "$keystore"
if [[ -n "$fingerprint" ]]; then
  printf '%s\n' "$fingerprint" > "$out_dir/fingerprint.txt"
fi
echo "F-Droid repository built in $out_dir with $apk_count APK(s)"
