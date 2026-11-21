# syntax=docker/dockerfile:1
#
# The Hunch Home image: Hunch's model, its rules, the Wyoming conversation agent and the owner's page in one
# process. One image serves both forms: the Home Assistant app (app/hunch_home/) and Docker beside Home
# Assistant (compose.example.yaml).
#
# The build context is the folder this file is in, for the app's own code and page. The model, its runtime and
# their licence files are NOT in this repository: they come from a second build context named "model", a copy
# of the PUBLIC release, xevall/hunch-1-home-ONNX at the released tag (typed/, hunch_home/, NOTICE, LICENSE and
# encoder/LICENSE; the other build folders are not read). So the image holds exactly what anyone can download,
# with the same credit file. Docker 23 or newer (BuildKit) is needed. From this folder:
#
#   docker build --build-context model=path/to/hunch-1-home-ONNX -t hunch-home:0.2.0 .
#
# For both processors at once (Raspberry Pi and PC), with buildx:
#
#   docker buildx build --platform linux/arm64,linux/amd64 \
#     --build-context model=path/to/hunch-1-home-ONNX -t REGISTRY/yungbose/hunch-home:0.2.0 .
#
# Home Assistant's own build of a local app cannot be given a second context. For that, the release's folder
# is copied into the app's folder as model/ (the test machine's install script does this), and the stage below
# takes it from there. Given --build-context model=..., BuildKit replaces that stage and model/ is not read.
#
# Dockerfile.dockerignore beside this file keeps everything else in the folder out of the build.

ARG PYTHON_IMAGE=python:3.13.18-slim-trixie
ARG TARGETARCH

# The public release: replaced by --build-context model=FOLDER, or read from model/ in this folder. With
# neither, this COPY fails with "not found", which means: no model was given.
FROM scratch AS model
COPY model/ /

# Check the model against its manifest and its version, and make the faster copy of its graph, once, on the
# machine that builds (plain Python, no packages). Only the files the app reads go on.
FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS prepare
ARG HUNCH_VERSION=1.1
COPY hunch_app /app/hunch_app
COPY --from=model /typed/ /model/
# The credit file and the licences travel with the model, laid out as NOTICE names them.
COPY --from=model /NOTICE /LICENSE /out/licenses/
COPY --from=model /hunch_home/LICENSE /out/licenses/hunch_home/LICENSE
COPY --from=model /encoder/LICENSE /out/licenses/encoder/LICENSE
COPY --from=model /hunch_home/ /out/hunch_home/
RUN if [ ! -f /model/manifest.json ]; then \
      echo "The model context has no typed/manifest.json. Build with --build-context" \
           "model=/path/to/hunch-1-home-ONNX (the public release's folder), or copy that folder here as" \
           "model/." >&2; \
      exit 1; \
    fi \
 && cd /app && python -m hunch_app.model prepare /model "${HUNCH_VERSION}" \
 && mkdir -p /out/model \
 && cp -R /model/manifest.json /model/model_q4.onnx /model/model_q4.onnx_data /model/model_q4_al4.onnx \
          /model/tokenizer.json /model/lookup /out/model/ \
 && rm -rf /out/hunch_home/__pycache__ \
 && chmod -R a+rX,go-w /out

FROM ${PYTHON_IMAGE} AS app
ARG HUNCH_VERSION=1.1
ARG IMAGE_VERSION=0.2.0
# ORT_DISABLE_TELEMETRY: ONNX Runtime's official builds send trace events to Microsoft unless it is set.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    ORT_DISABLE_TELEMETRY=1 \
    HUNCH_MODEL_DIR=/model \
    HUNCH_DATA=/data \
    HUNCH_THREADS=2
# Every package, with its checksum and as a ready-built wheel: pip refuses anything that is not byte for byte
# what was locked, and builds nothing from source.
COPY requirements.lock /app/requirements.lock
# The sound tools listening reads the microphone with (only when it is switched on): parec, from Home Assistant's
# sound system, and arecord, for an ALSA device in Docker. Debian's own signed packages, with no recommends.
RUN apt-get update \
 && apt-get install -y --no-install-recommends pulseaudio-utils alsa-utils \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /app/requirements.lock \
 && useradd --system --user-group --uid 10001 --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin hunch \
 && mkdir -p /data \
 && chown hunch:hunch /data
COPY --from=prepare /out/hunch_home /app/hunch_home
COPY --from=prepare /out/licenses /licenses
# Listening's credits: the speech model it fetches on first use, and the libraries it runs with.
COPY hunch_app/listen/NOTICE /licenses/listening/NOTICE
COPY hunch_app /app/hunch_app
COPY page /app/page
# Everything belongs to root and is read-only to the app's own user: it never writes to its code or model.
# (The model's modes are set in the prepare stage, so no second 250 MB layer is made here.)
COPY --from=prepare /out/model /model
RUN chmod -R a+rX,go-w /app
# No USER line: Home Assistant's Supervisor starts an app as the image's user and gives it a /data only root may
# write to. So the image starts as root and the app's first step is to give /data to the ordinary user `hunch`
# and become it (__main__.py, become_app_user): it never runs as root, under the Supervisor or in Docker.
# compose.example.yaml starts it as that user directly, with every capability dropped.
WORKDIR /app
# The Wyoming port (never publish it to a network: it has no sign-in) and the page.
EXPOSE 10500 8099
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-m", "hunch_app", "health"]
ENTRYPOINT ["python", "-m", "hunch_app"]
LABEL org.opencontainers.image.title="Hunch Home" \
      org.opencontainers.image.description="Hunch-${HUNCH_VERSION} Home: home commands in your own words, for Home Assistant" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.vendor="xevall" \
      org.opencontainers.image.source="https://github.com/yungbose/hunch-home" \
      org.opencontainers.image.licenses="Apache-2.0 AND CC-BY-NC-4.0" \
      hunch.model.version="${HUNCH_VERSION}" \
      hunch.model.licence="CC-BY-NC-4.0; commercial use by permission" \
      hunch.code.licence="Apache-2.0" \
      io.hass.type="app" \
      io.hass.version="${IMAGE_VERSION}"

# Home Assistant reads the processor from a label, under its own names for them.
FROM app AS app-arm64
LABEL io.hass.arch="aarch64"

FROM app AS app-amd64
LABEL io.hass.arch="amd64"

FROM app-${TARGETARCH}
