FROM bluenviron/mediamtx:1 AS mediamtx

# The official image is minimal; curl is added only for a real health probe.
FROM alpine:3.21
RUN apk add --no-cache curl
COPY --from=mediamtx /mediamtx /mediamtx
COPY mediamtx.yml /mediamtx.yml
ENTRYPOINT ["/mediamtx"]
