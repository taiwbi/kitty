Portable Linux kitty and standalone kitten builds from this tag.

Fixes in v.0.49.2.15:

- Set the default GTK window class and Wayland application ID to `com.taiwbi.kitty`.
- Draw the new-tab glyph using the theme foreground without external icon files or SVG loaders; use a flat button with no persistent background.
- Add `StartupWMClass=com.taiwbi.kitty` to both desktop launchers.
- Check the rendered new-tab glyph in the frozen portable runtime under Xvfb.

- **kitty:** Linux amd64/arm64 bundles (`.txz`).
- **kitten:** Linux amd64, arm64, arm, and 386.
- **Source:** `.tar.xz` including HTML documentation and man pages.
- **Verification:** `SHA256SUMS` and an SPDX dependency SBOM.

Linux bundles include the Python runtime and application dependencies. As with upstream builds, they need system graphics, fontconfig, and D-Bus libraries. Downloaded standalone kitten files may need `chmod +x` before use.
