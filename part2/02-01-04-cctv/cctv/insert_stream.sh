#!/bin/sh

curl -X POST \
  http://localhost:3030/fc/update \
  --data-urlencode 'update=
PREFIX cctv: <http://k.fc/onto/cctv#>
INSERT DATA {
  cctv:L010009 cctv:hasStreamUrl <https://topiscctv1.eseoul.go.kr/sd2/ch98.stream/playlist.m3u8> .
}'
