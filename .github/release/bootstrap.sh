#!/usr/bin/env bash
set -euo pipefail

platform="${1:?Expected linux or macos}"
case "$platform" in
    linux)
        export BYPY_ROOT=/
        # Docker runs as root, while actions/checkout files belong to the runner.
        git config --global --add safe.directory "$PWD"
        case "$(uname -m)" in
            x86_64) export BYPY_ARCH=linux-64 ;;
            aarch64) export BYPY_ARCH=linux-arm64 ;;
            *) echo 'Unsupported Linux release architecture' >&2; exit 1 ;;
        esac
        ;;
    macos)
        export BYPY_ROOT=/Users/Shared/kitty-build BYPY_ARCH=macos
        export BYPY_UNIVERSAL=true BYPY_DEPLOY_TARGET=12.0 MACOSX_DEPLOYMENT_TARGET=12.0
        ;;
    *) echo 'Expected linux or macos' >&2; exit 1 ;;
esac

release_root="${BYPY_ROOT%/}"
release_prefix="$release_root/sw/sw"
mkdir -p "$release_prefix" "$release_root/sw/t" "$release_root/sw/downloads"
if [[ "$platform" == macos ]]; then
    ln -s "$PWD" "$release_root/src"
    ln -s "${BYPY_SOURCE:?Path to the pinned upstream bypy checkout}" "$release_root/bypy"
fi

download() {
    local url="$1" dest="$release_root/sw/downloads/$2"
    if [[ -n "${RELEASE_DOWNLOAD_CACHE:-}" && -f "$RELEASE_DOWNLOAD_CACHE/$2" ]]; then
        cp "$RELEASE_DOWNLOAD_CACHE/$2" "$dest"
    else
        curl --fail --location --retry 5 --retry-all-errors --connect-timeout 30 \
            --max-time 600 --output "$dest" "$url"
    fi
}

bundle="$BYPY_ARCH"
[[ "$platform" != macos ]] || bundle=macos-64
download "https://download.calibre-ebook.com/ci/kitty/$bundle.tar.xz" "$bundle.tar.xz"
tar -xf "$release_root/sw/downloads/$bundle.tar.xz" -C "$release_prefix"
export PATH="$release_prefix/bin:$PATH"
unset PYTHONPATH PYTHONHOME
if [[ "$platform" == linux ]]; then
    export LD_LIBRARY_PATH="$release_prefix/lib" PYTHONHOME="$release_prefix"
    export FONTCONFIG_PATH=/etc/fonts
fi
export SW="$release_prefix" SLANGC="$release_prefix/bin/slangc"
export PKG_CONFIG_PATH="$release_prefix/lib/pkgconfig"
if [[ "$platform" == macos ]]; then
    export PKGCONFIG_EXE="$release_prefix/bin/pkg-config"
else
    export PKGCONFIG_EXE=/usr/bin/pkg-config
fi
export CI=true PYTHONUNBUFFERED=1

download https://github.com/ryanoasis/nerd-fonts/releases/latest/download/NerdFontsSymbolsOnly.tar.xz nerd-fonts.tar.xz
mkdir -p fonts
tar -xf "$release_root/sw/downloads/nerd-fonts.tar.xz" -C fonts SymbolsNerdFontMono-Regular.ttf

# Build-only Python packages live outside the prefix which gets frozen/shipped.
download https://bootstrap.pypa.io/get-pip.py get-pip.py
docs_deps="$release_root/sw/docs-deps"
python "$release_root/sw/downloads/get-pip.py" --target "$docs_deps" --no-warn-script-location
PYTHONPATH="$docs_deps" python -m pip install --disable-pip-version-check \
    --no-warn-script-location --upgrade --target "$docs_deps" -r docs/requirements.txt
export RELEASE_DOCS_DEPS="$docs_deps"
# Exported OpenSSL's default CA path points at the maintainer's build prefix.
# The freezer downloads CA certificates with urllib, so supply a real trust store.
export SSL_CERT_FILE="$docs_deps/certifi/cacert.pem"
download https://curl.se/ca/cacert.pem cacert.pem

download https://github.com/fribidi/fribidi/releases/download/v1.0.16/fribidi-1.0.16.tar.xz fribidi-1.0.16.tar.xz
python .github/release/build.py
