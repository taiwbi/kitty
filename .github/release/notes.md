Portable Linux kitty and standalone kitten builds from this tag.

- **kitty:** Linux amd64/arm64 bundles (`.txz`).
- **kitten:** Linux amd64, arm64, arm, and 386.
- **Source:** `.tar.xz` including HTML documentation and man pages.
- **Verification:** `SHA256SUMS` and an SPDX dependency SBOM.

Linux bundles include the Python runtime and application dependencies. As with upstream builds, they need system graphics, fontconfig, and D-Bus libraries. Downloaded standalone kitten files may need `chmod +x` before use.
