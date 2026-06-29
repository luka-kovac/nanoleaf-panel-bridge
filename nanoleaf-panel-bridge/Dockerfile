FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache jq
RUN pip install --no-cache-dir requests paho-mqtt

COPY bridge.py /app/bridge.py
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
