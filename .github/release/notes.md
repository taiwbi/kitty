Portable Linux kitty and standalone kitten builds from this tag.

Fixes in v0.49.2.13:

- Fix portable-build startup crashes and spurious systemd allocation errors caused by loading a second, incompatible host systemd library.
- Fix intermittent GTK crashes when native timers or GLFW events run inside a Python GTK event loop.
- Keep Wayland background blur aligned with the visible window, inside its shadow margins.
- Wait for the terminal's initial allocation, ignore zero-sized GTK resizes, and avoid the inactive foreground shader uniform warning.

- **kitty:** Linux amd64/arm64 bundles (`.txz`).
- **kitten:** Linux amd64, arm64, arm, and 386.
- **Source:** `.tar.xz` including HTML documentation and man pages.
- **Verification:** `SHA256SUMS` and an SPDX dependency SBOM.

Linux bundles include the Python runtime and application dependencies. As with upstream builds, they need system graphics, fontconfig, and D-Bus libraries. Downloaded standalone kitten files may need `chmod +x` before use.
