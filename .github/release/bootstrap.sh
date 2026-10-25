#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 0 ]] || { echo 'This release builder supports Linux only' >&2; exit 1; }
export BYPY_ROOT=/
# Docker runs as root, while actions/checkout files belong to the runner.
git config --global --add safe.directory "$PWD"
case "$(uname -m)" in
    x86_64) export BYPY_ARCH=linux-64 ;;
    aarch64) export BYPY_ARCH=linux-arm64 ;;
    *) echo 'Unsupported Linux release architecture' >&2; exit 1 ;;
esac

release_root="${BYPY_ROOT%/}"
release_prefix="$release_root/sw/sw"
mkdir -p "$release_prefix" "$release_root/sw/t" "$release_root/sw/downloads"

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
download "https://download.calibre-ebook.com/ci/kitty/$bundle.tar.xz" "$bundle.tar.xz"
tar -xf "$release_root/sw/downloads/$bundle.tar.xz" -C "$release_prefix"
export PATH="$release_prefix/bin:$PATH"
unset PYTHONPATH PYTHONHOME
export LD_LIBRARY_PATH="$release_prefix/lib" PYTHONHOME="$release_prefix"
export FONTCONFIG_PATH=/etc/fonts
export SW="$release_prefix" SLANGC="$release_prefix/bin/slangc"
export PKG_CONFIG_PATH="$release_prefix/lib/pkgconfig"
export PKGCONFIG_EXE=/usr/bin/pkg-config
export CI=true PYTHONUNBUFFERED=1 KITTY_REQUIRE_GTK=1
# The exported Python .pc files can retain the temporary prefix used to build
# Python. Meson uses these when compiling Pycairo/PyGObject extensions.
sed -i "s|^prefix=.*|prefix=$release_prefix|" "$release_prefix"/lib/pkgconfig/python*.pc
# GTK needs Cairo's Xlib and HarfBuzz's GLib/GObject integration, which the
# exported libraries disable. Replace both with the builder's complete builds,
# including their metadata so GTK's exact HarfBuzz dependencies remain coherent.
for package in cairo harfbuzz; do
    system_libdir="$(env -u PKG_CONFIG_PATH -u PKG_CONFIG_LIBDIR /usr/bin/pkg-config --variable=libdir "$package")"
    rm "$release_prefix"/lib/lib"$package"*.so*
    cp -a "$system_libdir"/lib"$package"*.so* "$release_prefix/lib/"
    cp "$system_libdir"/pkgconfig/"$package"*.pc "$release_prefix/lib/pkgconfig/"
done

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
# The distro's python3-gi targets its own Python ABI, not the exported Python
# used by the frozen runtime. Build the bindings for the interpreter we ship.
PYTHONPATH="$docs_deps" python -m pip install --disable-pip-version-check \
    --no-warn-script-location --prefix "$release_prefix" 'pycairo==1.28.0' 'PyGObject==3.54.5'
# Exported OpenSSL's default CA path points at the maintainer's build prefix.
# The freezer downloads CA certificates with urllib, so supply a real trust store.
export SSL_CERT_FILE="$docs_deps/certifi/cacert.pem"
download https://curl.se/ca/cacert.pem cacert.pem

download https://github.com/fribidi/fribidi/releases/download/v1.0.16/fribidi-1.0.16.tar.xz fribidi-1.0.16.tar.xz
python .github/release/build.py
