# 构建上下文是仓库根目录：docker build -f inference/Dockerfile .
# CDK -c buildTrackerImage=true 用这份文件。主机上的扁平 bootstrap/bake/ 不要直接换上来。
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 libxcb1 libgl1 ffmpeg fonts-wqy-microhei \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/track
COPY inference/requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt \
  && python3 -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(det_limit_side_len=640)"
COPY inference/track.py inference/run_job.py inference/dub.py ./
COPY pipeline/vision/scoreboard.py inference/clips.json ./vision/
COPY web/game.html web/game.js web/styles.css ./web/

# 权重不进镜像。作业从 s3://$MEDIA_BUCKET/bootstrap/models/ 拉取。
CMD ["python3", "/opt/track/run_job.py"]
