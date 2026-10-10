# 演示视频：一条命令重录

1920×1080，Chromium。脚本打开 CloudFront 上的播放器，按镜头操作，再烧中文字幕。

画面顺序：打开播放器并播放 `--clip`、人名和记分牌、时间线来回跳、进球后定格 5 秒、中文和英文音轨各听一段、场上十人、打开 `/readme` 滚过数据管道和 AWS 服务表。服务表会停在认人那一行。计划时长 2 分钟。

没加 `--polly` 时保留页面原声（播放器暂停和定格的地方就是静音），只烧字幕。加了 `--polly` 后，用 Amazon Polly Neural 知语（`Zhiyu`，`cmn-CN`）按句合成，叠在压低的页面原声上。

解说词在 `narration.md`。事实只对照 `docs/pipeline-map.md`、`docs/architecture.md`、`web/readme.html`。

## 19:00 重录

在 macOS、仓库根目录执行。考试片已经出现在播放器上之后，把 `flagg` 换成 `web/replay/clips.js` 里新片子的键（地址栏 `?clip=` 的值，和播放器顶部那一排一致）。

```bash
export AWS_PROFILE=nba
export AWS_REGION=us-east-1
python3 scripts/demo/record.py \
  --base-url https://d2vzd2batw1eqe.cloudfront.net \
  --clip flagg \
  --polly \
  --out "$HOME/Desktop/deepcourt-demo.mp4"
```

成片是 H.264，旁边会有同名的 `.srt`。mp4 不要提交进仓库。

第一次在这台 Mac 上跑，先装依赖：

```bash
python3 -m pip install -r scripts/demo/requirements.txt
python3 -m playwright install chromium
python3 -m playwright install ffmpeg
brew install ffmpeg
```

本机有 Google Chrome 时会优先用它。没有 Chrome 再用上一行装好的 Chromium。字幕字体在 macOS 上用苹方。

`--polly` 会读当前环境里的 `AWS_PROFILE` 和 `AWS_REGION`。凭证不通就不要加这个开关，否则整条命令失败，不会悄悄交一支没配音的片子。

## 这支初版

训练片 `flagg`，没有 AWS 凭证，所以不加 `--polly`。已交的初版成片仍是上一版字幕。`narration.srt` 是现在这份口播，重录时以成片旁边新生成的 SRT 为准。

```bash
python3 scripts/demo/record.py \
  --base-url https://d2vzd2batw1eqe.cloudfront.net \
  --clip flagg \
  --out demo.mp4
```

## 参数

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--base-url` | `https://d2vzd2batw1eqe.cloudfront.net` | 播放器根路径。`/readme` 用同一域名 |
| `--clip` | `flagg` | 传给播放器的 `clip`。键来自 `web/replay/clips.js` |
| `--polly` | 关 | Polly Neural 知语，按句合成后叠上音轨 |
| `--out` | `scripts/demo/out/demo.mp4` | 成片路径 |
| `--region` | `AWS_REGION`，否则 `us-east-1` | Polly 区域 |

`python3 scripts/demo/record.py --check` 不打开浏览器，只检查字幕时间和音轨拼接。
