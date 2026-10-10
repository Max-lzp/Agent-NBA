#!/bin/sh
# 把解说 MP3 混进成片。需要本机 ffmpeg。视频没有音轨时改为只铺解说。
set -eu
if [ "$#" -ne 3 ]; then
  echo "用法: commentary/mix.sh 成片.mp4 解说.mp3 输出.mp4" >&2
  exit 1
fi
video=$1
voice=$2
output=$3
if ffmpeg -i "$video" -hide_banner 2>&1 | grep -q 'Audio:'; then
  ffmpeg -y -i "$video" -i "$voice" \
    -filter_complex "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]" \
    -map 0:v -map "[a]" -c:v copy "$output"
else
  ffmpeg -y -i "$video" -i "$voice" -map 0:v -map 1:a -c:v copy -shortest "$output"
fi
