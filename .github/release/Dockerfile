# Match the glibc baseline used by kitty's upstream Linux release builder.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    build-essential ca-certificates chrpath curl git pkg-config xz-utils \
    libfontconfig1-dev libdbus-1-dev libgl1-mesa-dev libxcursor-dev \
    libxi-dev libxinerama-dev libxrandr-dev libx11-xcb-dev libxcb-xkb-dev \
    libsystemd-dev ncurses-bin \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/go/bin:${PATH}" GOTOOLCHAIN=local
WORKDIR /src
CMD ["bash", ".github/release/bootstrap.sh", "linux"]
