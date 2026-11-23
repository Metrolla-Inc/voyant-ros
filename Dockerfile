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

# Set working directory
WORKDIR /workspace

# Download and install the voyant-api release packages from GitHub, pinned: this driver is the
# vendor's voyant-ros v1.0.1, written against voyant-api 1.0.1 (CarbonClient; Carbon firmware
# FPGA v1.5.6 / MCU v4.1.0). The vendor's Dockerfile takes whatever release is latest; pinned here
# by version and sha256 so a build is the same on any day (docs/VENDOR_DIFF.md). voyant-api is
# proprietary (binary only), redistributed with Voyant's written consent (owner, 22 Nov 2026).
# Cap'n Proto is no longer built from source: voyant-api 1.0 does not need it (vendor fef04f0).
ARG VOYANT_API_VERSION=1.0.1
ARG VOYANT_API_SHA256=8058990f10d66670ea2aca3e4f34c132cdc66a34c4e755ec2d04685a0379ebd0
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
