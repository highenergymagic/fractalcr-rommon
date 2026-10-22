FROM debian:bookworm@sha256:2c037a04925515fdd6ea85ea14a682d0e79931f5e9f5d07b6dbfc6ba12f9e858
COPY boot/debian.sources /etc/apt/sources.list.d/debian.sources
# The minimal base has no CA bundle. Bootstrap it through Debian's signed APT
# metadata over HTTP, then require HTTPS for the remaining pinned packages.
RUN sed -i 's|https://|http://|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o APT::Update::Error-Mode=any update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources
RUN apt-get -o APT::Update::Error-Mode=any update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential bc bison flex cpio rsync file unzip wget curl ca-certificates \
    python3 python3-pexpect python3-cryptography python3-pefile python3-venv python3-setuptools \
    libssl-dev libelf-dev libncurses-dev gawk git xz-utils patch \
    qemu-system-x86 ovmf sbsigntool efitools dosfstools mtools \
    systemd-boot-efi uuid-runtime kmod faketime && rm -rf /var/lib/apt/lists/*
COPY boot/requirements.txt /tmp/firmware-requirements.txt
RUN python3 -m venv --system-site-packages /opt/firmware-tools \
    && /opt/firmware-tools/bin/pip install --no-cache-dir --no-deps --require-hashes -r /tmp/firmware-requirements.txt \
    && /opt/firmware-tools/bin/pip check
ENV PATH="/opt/firmware-tools/bin:${PATH}"
WORKDIR /work
