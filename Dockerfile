ARG ROS_DISTRO=jazzy
FROM ros:${ROS_DISTRO}-ros-core AS build-env

# Set shell options for better error handling
SHELL ["/bin/bash", "-c"]

ARG RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ARG VIZ_BRIDGE="false"
ENV DEBIAN_FRONTEND=noninteractive \
    RMW_IMPLEMENTATION_ARG=${RMW_IMPLEMENTATION} \
    VIZ_BRIDGE_ARG=${VIZ_BRIDGE} \
    BUILD_HOME=/var/lib/build

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends --fix-missing \
    python3-colcon-common-extensions \
    ros-${ROS_DISTRO}-ros2launch \
    ros-${ROS_DISTRO}-pcl-ros \
    ros-${ROS_DISTRO}-rosbag2 \
    ros-${ROS_DISTRO}-ament-index-cpp \
    libpcl-dev \
    build-essential \
    apt-utils \
    cmake \
    curl \
    g++ \
    pkg-config \
    jq \
    libwayland-dev \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create workspace directory
WORKDIR ${BUILD_HOME}

RUN if [ "$RMW_IMPLEMENTATION_ARG" = "rmw_cyclonedds_cpp" ]; then \
    apt-get update && \
    apt-get install -y ros-${ROS_DISTRO}-rmw-cyclonedds-cpp && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# Install Cap'n Proto from source used by Voyant API
# (checksum recorded 2026-11-22; the download was not checked before)
ARG CAPNP_SHA256=07167580e563f5e821e3b2af1c238c16ec7181612650c5901330fa9a0da50939
WORKDIR /tmp
RUN curl -fsSLO https://capnproto.org/capnproto-c++-1.1.0.tar.gz && \
    echo "${CAPNP_SHA256}  capnproto-c++-1.1.0.tar.gz" > capnp.sha256 && sha256sum -c capnp.sha256 && \
    tar zxf capnproto-c++-1.1.0.tar.gz && \
    cd capnproto-c++-1.1.0 && \
    ./configure && \
    make -j6 && \
    make install && \
    ldconfig && \
    cd .. && \
    rm -rf capnproto-c++-1.1.0* capnp.sha256

# Set working directory
WORKDIR /workspace

# Download and install the voyant-api release packages from GitHub, pinned: this driver is written
# against voyant-api 0.4.4 (the vendor commit this fork is based on, 0d16e0a, "Update dockerfile
# for v0.4.4 voyant-api release"). The image used to take whatever release was latest: every build
# of this fork since May 2026 failed (on 21 Nov because voyant-api 1.0.1 no longer ships
# voyant_frame_wrapper.hpp). Updating is the vendor update in docs/VENDOR_UPDATE_PLAN.md.
# voyant-api is proprietary (binary only; see that file).
ARG VOYANT_API_VERSION=0.4.4
ARG VOYANT_API_SHA256=1c2b69f7e170a0299d1f0293cff2f6f8bbd3a1897f518d8960dee3dfa1f548dd
RUN ARCH=$(dpkg --print-architecture) && \
    TARBALL_URL="https://github.com/Voyant-Photonics/voyant-sdk/releases/download/v${VOYANT_API_VERSION}/voyant-api-debian-v${VOYANT_API_VERSION}.tar.gz" && \
    wget -O /tmp/voyant-api.tar.gz "$TARBALL_URL" && \
    echo "${VOYANT_API_SHA256}  /tmp/voyant-api.tar.gz" > /tmp/voyant-api.sha256 && \
    sha256sum -c /tmp/voyant-api.sha256 && \
    mkdir -p /tmp/voyant-api-extract && \
    tar -xzf /tmp/voyant-api.tar.gz -C /tmp/voyant-api-extract && \
    API_DEB=$(find /tmp/voyant-api-extract -name "voyant-api_*${ARCH}.deb" | head -n 1) && \
    DEV_DEB=$(find /tmp/voyant-api-extract -name "voyant-api-dev_*${ARCH}.deb" | head -n 1) && \
    if [ -z "$API_DEB" ] || [ -z "$DEV_DEB" ]; then echo "Required .deb packages for ARCH=$ARCH not found" >&2; exit 1; fi && \
    apt-get install -y "$API_DEB" "$DEV_DEB" && \
    apt-get clean && \
    rm -rf /tmp/voyant-api.tar.gz /tmp/voyant-api.sha256 /tmp/voyant-api-extract /var/lib/apt/lists/*

# Create a workspace directory
WORKDIR /ros2_ws/src

# Copy contents of current dir into the workspace
COPY . ./voyant-ros

# Go back to workspace root
WORKDIR /ros2_ws

# Build the ROS2 package
RUN source /opt/ros/${ROS_DISTRO}/setup.sh && colcon build --symlink-install

# Install foxglove dependencies if VIZ_BRIDGE_ARG is true
RUN if [ "$VIZ_BRIDGE_ARG" = "true" ]; then \
    apt-get update && \
    apt-get install -y ros-${ROS_DISTRO}-foxglove-bridge && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# Set up entrypoint
RUN echo "source /ros2_ws/install/setup.bash" >> ~/.bashrc

# Metrolla driver contract (opt-in with METROLLA_DRIVER_CONTRACT=1; docs/METROLLA_DRIVER_CONTRACT.md).
# selfcheck compares the parameters the contract sets with the ones the node in this image
# declares, so a vendor update that renames one fails here, not on a unit.
COPY docker/contract-entrypoint.sh docker/voyant_env.py /opt/metrolla/voyant/
RUN chmod 0755 /opt/metrolla/voyant/contract-entrypoint.sh /opt/metrolla/voyant/voyant_env.py \
    && python3 -I /opt/metrolla/voyant/voyant_env.py selfcheck

# Entry point. Without the contract it runs the released entrypoint command, unchanged (it was
# the shell-form ENTRYPOINT here; docker/voyant_entrypoint.sh holds it byte for byte).
COPY docker/voyant_entrypoint.sh /voyant_entrypoint.sh
RUN chmod 0755 /voyant_entrypoint.sh
ENTRYPOINT ["/voyant_entrypoint.sh"]
