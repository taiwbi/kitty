Portable kitty and standalone kitten builds from this tag.

- **kitty:** Linux amd64/arm64 bundles (`.txz`) and a universal macOS application (`.dmg`, Intel + Apple Silicon).
- **kitten:** macOS amd64/arm64; DragonFly amd64; FreeBSD, NetBSD, and OpenBSD amd64/arm64; Linux amd64, arm64, arm, and 386.
- **Source:** `.tar.xz` including HTML documentation and man pages.
- **Verification:** `SHA256SUMS` and an SPDX dependency SBOM.

Linux bundles include the Python runtime and application dependencies. As with upstream builds, they need system graphics, fontconfig, and D-Bus libraries. Downloaded standalone kitten files may need `chmod +x` before use.

The macOS application is ad-hoc signed, without Apple Developer ID signing or notarization. macOS may require allowing it in **System Settings → Privacy & Security** when first opened.
