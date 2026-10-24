FROM ubuntu:24.04

ARG RELEASE_GTK=0

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    build-essential ca-certificates chrpath curl git pkg-config xz-utils \
    libfontconfig1-dev libdbus-1-dev libgl1-mesa-dev libxcursor-dev \
    libxi-dev libxinerama-dev libxrandr-dev libx11-xcb-dev libxcb-xkb-dev \
    libsystemd-dev ncurses-bin \
    libgtk-4-dev libadwaita-1-dev python3-dev libxxhash-dev liblcms2-dev \
    libsimde-dev spirv-tools \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$RELEASE_GTK" = 1 ]; then \
        pkg-config --exists gtk4 libadwaita-1 && \
        pkg-config --atleast-version=1.4 libadwaita-1; \
    fi

ENV PATH="/opt/go/bin:${PATH}" GOTOOLCHAIN=local
WORKDIR /src
CMD ["bash", ".github/release/bootstrap.sh"]
