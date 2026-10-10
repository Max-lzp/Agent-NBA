FROM public.ecr.aws/docker/library/python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
  && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/track
COPY requirements.txt track.py ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

CMD ["python3", "track.py"]
