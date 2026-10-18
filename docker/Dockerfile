# El instalador resuelve y registra el digest ARM64 antes de construir.
ARG JAVA_BASE
FROM ${JAVA_BASE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10000 minecraft \
    && useradd --uid 10000 --gid minecraft --no-create-home minecraft
COPY --chmod=0555 entrypoint.sh /opt/mclan/entrypoint.sh
COPY --chmod=0555 bluemap-setup.sh /opt/mclan/bluemap-setup.sh
COPY --chmod=0444 probe.py /opt/mclan/probe.py
WORKDIR /data
USER 10000:10000
EXPOSE 25565/tcp 19132/udp 19132/tcp 19133/udp 8100/tcp
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/mclan/entrypoint.sh"]
