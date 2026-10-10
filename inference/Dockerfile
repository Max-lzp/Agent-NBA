FROM public.ecr.aws/docker/library/python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 libxcb1 libgl1 ffmpeg fonts-wqy-microhei \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/track
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt \
  && python3 -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(det_limit_side_len=640)"
COPY track.py run_job.py dub.py ./
COPY scoreboard.py clips.json ./vision/
COPY web/game.html web/game.js ./web/
COPY models/ ./models/

CMD ["python3", "/opt/track/run_job.py"]
