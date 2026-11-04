Portable Linux kitty and standalone kitten builds from this tag.

Changes in 0.50.0.1:

- Fix GTK startup failing with `RuntimeError: The GTK window callback did not return a GTK window` on GNOME Wayland.
- Exercise full GTK terminal startup in the frozen portable runtime before publishing either Linux architecture.

Changes in 0.50.0.0:

- Set the default GTK window class and Wayland application ID to `com.taiwbi.kitty`.
- Draw the new-tab glyph using the theme foreground without external icon files or SVG loaders; use a flat button with no persistent background.
- Add `StartupWMClass=com.taiwbi.kitty` to both desktop launchers.
- Check the rendered new-tab glyph in the frozen portable runtime under Xvfb.
- Avoid duplicate Wayland background-effect requests from GTK windows, which could make Fedora's compositor terminate the display connection during startup.
- Run CI builds and package tests on Linux only.

- **kitty:** Linux amd64/arm64 bundles (`.txz`).
- **kitten:** Linux amd64, arm64, arm, and 386.
- **Source:** `.tar.xz` including HTML documentation and man pages.
- **Verification:** `SHA256SUMS` and an SPDX dependency SBOM.

Linux bundles include the Python runtime and application dependencies. As with upstream builds, they need system graphics, fontconfig, and D-Bus libraries. Downloaded standalone kitten files may need `chmod +x` before use.
