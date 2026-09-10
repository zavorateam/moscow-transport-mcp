#!/bin/bash
API_KEY="c75824405e5dbe711fc0a432e53cf2c6"
DATASET=752
TOP=1000
SKIP=0

COUNT=$(curl -s "https://apidata.mos.ru/v1/datasets/${DATASET}/count?api_key=${API_KEY}")
echo "Всего записей: ${COUNT}"

> bus_stops_all.geojson
echo '{"type":"FeatureCollection","features":[' > bus_stops_all.geojson
FIRST=1

while [ $SKIP -lt $COUNT ]; do
  curl -s "https://apidata.mos.ru/v1/datasets/${DATASET}/features?\$top=${TOP}&\$skip=${SKIP}&api_key=${API_KEY}" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
feats = data.get('features', [])
first = ${FIRST}
if feats:
    sep = '' if first else ','
    print(sep + ','.join(json.dumps(f) for f in feats))
" >> bus_stops_all.geojson
  FIRST=0
  SKIP=$((SKIP + TOP))
  echo "Загружено: ${SKIP} из ${COUNT}"
done

echo ']}' >> bus_stops_all.geojson
echo "Готово: bus_stops_all.geojson"
