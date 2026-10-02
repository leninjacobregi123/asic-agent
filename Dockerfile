# ASIC agent — the whole pipeline in one image.
#
# Base: the pinned OpenROAD-flow-scripts image (OpenROAD, Yosys, the sky130hd
# platform files), so place-and-route runs inside this container rather than in
# a second one. Added: OSS CAD Suite for Verilator (pinned release), the
# Python dependencies, and the application. No model runs in the image; the
# agents call model provider APIs with keys supplied at run time.
#
#   docker compose up -d --build        (see docker-compose.yml, docs/DOCKER.md)

ARG ORFS_IMAGE=openroad/orfs:26Q3-705-gecb3cfdeb
FROM ${ORFS_IMAGE}

ARG OSS_CAD_RELEASE=2026-09-25
ARG OSS_CAD_VERSION=20260925
ARG ORFS_IMAGE
RUN curl -fsSL -o /tmp/oss-cad-suite.tgz \
      "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_CAD_RELEASE}/oss-cad-suite-linux-x64-${OSS_CAD_VERSION}.tgz" \
 && tar -xzf /tmp/oss-cad-suite.tgz -C /opt \
 && rm -rf /tmp/oss-cad-suite.tgz /opt/oss-cad-suite/examples

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt "tomli>=2; python_version < '3.11'" \
 && useradd --create-home --uid 1000 --shell /bin/bash asic \
 && mkdir -p /home/asic/.config/asic-agent /app \
 && chown -R asic:asic /home/asic /app

WORKDIR /app
COPY --chown=asic:asic . /app
# Mount points for the named volumes must exist (owned by the app user) in the
# image; otherwise Docker creates them root-owned and the app cannot write.
RUN mkdir -p /app/runs/.console /app/data/index && chown -R asic:asic /app/runs /app/data \
 && ln -s /app/scripts/asic-agent /usr/local/bin/asic-agent

# Toolchain locations inside the image (scripts/env.sh reads these).
ENV OSS_CAD_SUITE=/opt/oss-cad-suite \
    LIB_TT=/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib \
    PDK_ROOT=/OpenROAD-flow-scripts/flow/platforms \
    ORFS_IMAGE=${ORFS_IMAGE} \
    ORFS_NATIVE=1 \
    BUILD_DIR=/tmp/asic-build \
    HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    ASIC_AGENT_SUPERVISE_KB=1

USER asic
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -fs "http://127.0.0.1:8080/api/v1/health?deep=1" || exit 1
CMD ["scripts/start_app.sh"]
