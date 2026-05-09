#!/bin/sh

curl -X POST \
  http://localhost:3030/doverenc/update \
  --data-urlencode 'update=
PREFIX cctv: <http://k.doverenc/onto/cctv#>
INSERT DATA {
  cctv:L010009 cctv:hasStreamUrl <https://topiscctv1.eseoul.go.kr/sd2/ch98.stream/playlist.m3u8> .
}'