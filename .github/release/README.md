# Tag releases

`.github/workflows/release.yml` runs only on a pushed tag. It builds the tagged
commit and publishes the downloads on that tag's GitHub Release. Branch pushes,
pull requests, and manual dispatches do not start this workflow. Existing test
workflows retain their existing triggers.

The binary matrix matches [upstream v0.49.2](https://github.com/kovidgoyal/kitty/releases/tag/v0.49.2):

| Program | OS | Architectures | Download |
| --- | --- | --- | --- |
| kitty | Linux | amd64, arm64 | `kitty-<version>-x86_64.txz`, `kitty-<version>-arm64.txz` |
| kitty | macOS | universal amd64 + arm64 | `kitty-<version>.dmg` |
| kitten | Darwin | amd64, arm64 | `kitten-darwin-<arch>` |
| kitten | DragonFly | amd64 | `kitten-dragonfly-amd64` |
| kitten | FreeBSD, NetBSD, OpenBSD | amd64, arm64 | `kitten-<os>-<arch>` |
| kitten | Linux | amd64, arm64, arm, 386 | `kitten-linux-<arch>` |

`amd64` and `x86_64` refer to the same architecture; `386` is 32-bit x86, and
`arm` is 32-bit ARM (Go's default ARMv7). Upstream does not release kitty GUI
bundles for BSD or 32-bit platforms. The release also contains a source archive
with HTML/man documentation, an SPDX dependency SBOM, and `SHA256SUMS`.
Asset versions come from `kitty/constants.py`; the GitHub release name is the tag.

## Build order and dependencies

1. Check out the tag with history for documentation commit links. Install the
   exact Go toolchain declared in `go.mod` using `actions/setup-go`.
2. Fetch the pinned `kovidgoyal/bypy` commit. GitHub Actions are pinned to commits
   as well. Linux uses native amd64/arm64 runners and an Ubuntu 24.04 container
   with GTK 4 and libadwaita 1.4 or newer. macOS uses the macOS 26 runner's Xcode.
3. Extract upstream's exported dependency bundles into their original prefixes
   (`/sw/sw` or `/Users/Shared/kitty-build/sw/sw`). This preserves library paths,
   Python configuration, and macOS framework install names. Download the Nerd
   Font used by the build. Install documentation tools outside the shipped prefix,
   configure the exported OpenSSL trust store, and pre-download the shipped CA
   bundle with retries before compilation.
4. Build checksum-verified FriBidi 1.0.16 statically because this checkout needs
   it and the exported bundles do not include it. On macOS, compile both slices
   and combine the static library with `lipo`.
5. Build native kitty/kitten to generate Go code and shaders. Generate HTML/man
   documentation. Run kitty's existing `bypy` build/freezing hooks to bundle the
   Python runtime, native libraries, resources, and launchers. Preserve shared
   library aliases needed by Python extensions, including SQLite's unversioned
   library name. These hooks are
   called directly because bypy's normal `program` entry point deletes the
   dependency prefix and expects the maintainer's private package cache/VMs.
6. Smoke-test the frozen runtime, native extension imports, and both executables
   without build-time Python/library paths. Linux tests the extracted archive
   at a new location and repeats the check in a fresh container with no build
   prefix or toolchain. macOS re-signs relocated Mach-O binaries before executing
   the frozen launcher for code generation, then signs the completed app, verifies signatures
   and universal executables, and verifies the DMG.
7. On Linux amd64, run `setup.py build-static-binaries` **after** native code
   generation. This is the upstream cross-compilation path for all 13 standalone
   kitten targets, with `CGO_ENABLED=0` and the standalone-build linker flag.
8. Download all job artifacts in one publishing job. Validate the exact asset
   matrix, Go platform/revision metadata, ELF/Mach-O architectures, bundle contents,
   source archive, DMG trailer, and SBOM. Generate SHA-256 checksums. Upload assets
   to a draft release, then publish it. Failed jobs never publish a new release.

Build jobs have read-only repository permissions; only the final publishing job
has `contents: write`. No custom secrets or self-hosted runners are required.
GitHub Actions must be enabled in the fork and the account must have access to
the standard Linux ARM and macOS runners. Repository/organization policies must
allow this workflow's `contents: write` token permission.

The exported dependency bundles and font archives are upstream-managed download
URLs, as in upstream CI. The workflow checks the exported Python ABI against
`bypy/sources.json`. Their availability, GitHub services, and runner availability
remain external requirements.

## Publishing

Commit the workflow and release scripts before creating the tag. For this fork,
an example is:

```sh
git tag v0.49.2-fork.1
git push fork v0.49.2-fork.1
```

Any pushed tag starts the workflow; it need not match the source version exactly.
Re-running failed jobs resumes uploads with `--clobber`, so partial draft uploads
can be repaired. Artifacts also remain downloadable from the Actions run for seven
days. Standalone executables downloaded from GitHub may need `chmod +x`.

The macOS build uses an ad-hoc signature, without Developer ID signing or Apple
notarization. Allow the downloaded app in macOS Privacy & Security when necessary.
Upstream's GPG `.sig` files cannot be reproduced without its private signing key;
this fork publishes checksums instead.

## Local validation

On Linux with Docker and the Go version from `go.mod` installed:

```sh
git clone https://github.com/kovidgoyal/bypy.git /tmp/kitty-release-bypy
git -C /tmp/kitty-release-bypy checkout 5062c1f6e58fd8c62786f45e561cf96f9477ff67
docker build -t kitty-release .github/release
docker run --rm \
  -v "$PWD:/src" \
  -v /tmp/kitty-release-bypy:/bypy:ro \
  -v "$(go env GOROOT):/opt/go:ro" \
  -e RELEASE_STATIC_KITTENS=1 \
  kitty-release
```

This creates the Linux native bundle, all standalone kittens, source, and SBOM in
`dist/`. Run the equivalent container on an ARM64 host for the ARM64 kitty bundle.
The macOS build needs a Mac with Xcode; set `BYPY_SOURCE` to the pinned checkout,
then run `bash .github/release/bootstrap.sh macos` in a fresh build environment.
Once all platform outputs are in `dist/`, run `python3 .github/release/verify.py`.

For static checks, use `actionlint .github/workflows/release.yml`,
`bash -n .github/release/bootstrap.sh`, and `ruff check .github/release`.
