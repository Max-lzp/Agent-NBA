FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-pip \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/track
COPY requirements.txt track.py ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

CMD ["python3", "track.py"]
