# Linux tag releases

`.github/workflows/release.yml` runs when a tag is pushed. It builds Linux
releases for amd64 and arm64, then publishes those assets on the tag's GitHub
Release. The release also includes standalone Linux `kitten` binaries for
amd64, arm64, arm, and 386, a source archive with HTML/man documentation, an
SPDX dependency SBOM, and `SHA256SUMS`.

## Build order and dependencies

1. Check out the tag with history for documentation commit links and install the
   exact Go toolchain declared in `go.mod`.
2. Fetch the pinned `kovidgoyal/bypy` commit. Linux uses native amd64/arm64 runners
   and an Ubuntu 24.04 container with GTK 4 and libadwaita 1.4 or newer.
3. Extract upstream's exported Linux dependency bundle into `/sw/sw`. Download
   the Nerd Font used by the build. Install documentation tools outside the
   shipped prefix, configure the exported OpenSSL trust store, and pre-download
   the shipped CA bundle with retries before compilation.
4. Build checksum-verified FriBidi 1.0.16 statically because this checkout needs
   it and the exported bundles do not include it.
5. Build native kitty/kitten to generate Go code and shaders. Compile the GTK
   backend again with GTK disabled and warnings treated as errors. Generate
   HTML/man documentation and use the `bypy` Linux hooks to bundle the Python
   runtime, native libraries, resources, and launchers. Preserve shared-library
   aliases needed by Python extensions, including SQLite's unversioned library
   name. These hooks are called directly because `bypy`'s normal `program`
   entry point deletes the dependency prefix and expects the maintainer's
   private package cache/VMs.
6. Smoke-test the frozen runtime and extracted Linux archive without build-time
   Python/library paths. The workflow repeats the archive check in a fresh
   container with no build prefix or toolchain.
7. On Linux amd64, build standalone `kitten` binaries for Linux amd64, arm64,
   arm, and 386 after native code generation.
8. Download all Linux job artifacts in one publishing job. Validate the exact
   asset matrix, Go platform/revision metadata, ELF architectures, bundle
   contents, source archive, and SBOM. Generate SHA-256 checksums, upload assets
   to a draft release, then publish it.

Build jobs have read-only repository permissions; only the publishing job has
`contents: write`. GitHub Actions must be enabled in the fork and policies must
allow that workflow token permission. The dependency bundles and font archive
are upstream-managed download URLs, as in upstream CI. Their availability,
GitHub services, and runner availability remain external requirements.

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

This creates the Linux native bundle, standalone Linux kittens, source, and SBOM
in `dist/`. Run the equivalent container on an ARM64 host for the ARM64 kitty
bundle. Once all Linux outputs are in `dist/`, run
`python3 .github/release/verify.py`.

For static checks, use `actionlint .github/workflows/release.yml`,
`bash -n .github/release/bootstrap.sh`, and `ruff check .github/release`.
