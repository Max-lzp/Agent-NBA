"""Render an original, silent synthetic basketball example (requires FFmpeg).

No third-party footage, images, models, fonts or network downloads are used.
Timeline: possession 0s, pass 3s, three-point shot 6s, basket/score 9s.
"""
from pathlib import Path
import subprocess
import math

WIDTH, HEIGHT, FPS, DURATION = 640, 360, 24, 12
OUTPUT = Path(__file__).resolve().parents[1] / "portfolio/media/teaching.mp4"


def circle(frame, cx, cy, radius, color):
    rgb = bytes(color)
    for y in range(max(0, int(cy - radius)), min(HEIGHT, int(cy + radius + 1))):
        span = int(math.sqrt(max(0, radius * radius - (y - cy) ** 2)))
        left, right = max(0, int(cx - span)), min(WIDTH, int(cx + span + 1))
        offset = (y * WIDTH + left) * 3
        frame[offset:offset + (right - left) * 3] = rgb * (right - left)


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base = bytearray(bytes((28, 51, 34)) * (WIDTH * HEIGHT))
    # Court boundary, half court, key and three-point arc.
    for y in range(HEIGHT):
        for x in range(WIDTH):
            line = ((x in (15, 624) and 15 <= y <= 344)
                    or (y in (15, 344) and 15 <= x <= 624)
                    or (x == 200 and 15 <= y <= 344)
                    or (x == 495 and 110 <= y <= 250)
                    or (y in (110, 250) and 495 <= x <= 624)
                    or (abs(math.hypot(x - 590, y - 180) - 195) < 1.3 and x < 590))
            if line:
                index = (y * WIDTH + x) * 3
                base[index:index + 3] = bytes((116, 147, 104))
    circle(base, 590, 180, 11, (246, 167, 83))
    circle(base, 590, 180, 7, (28, 51, 34))
    filters = ",".join([
        "drawtext=text='SYNTHETIC TEACHING CLIP - NOT REAL GAME':x=24:y=25:fontsize=17:fontcolor=white",
        "drawtext=text='7':x=315:y=94:fontsize=17:fontcolor=black",
        "drawtext=text='11':x=419:y=74:fontsize=17:fontcolor=black",
        "drawtext=text='12':x=454:y=129:fontsize=17:fontcolor=black",
        "drawtext=text='A 72 - 70 B':x=24:y=305:fontsize=24:fontcolor=white:enable='lt(t,9)'",
        "drawtext=text='A 75 - 70 B':x=24:y=305:fontsize=24:fontcolor=white:enable='gte(t,9)'",
    ])
    command = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS), "-i", "pipe:0", "-vf", filters, "-an", "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUTPUT)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for number in range(FPS * DURATION):
            t = number / FPS
            frame = bytearray(base)
            for x, y, color in [(320, 100, (214, 250, 128)), (430, 80, (214, 250, 128)), (465, 135, (214, 225, 214))]:
                circle(frame, x, y, 18, color)
            if t < 3:
                x, y = 335, 112
            elif t < 4:
                f = t - 3
                x, y = 335 + 95 * f, 112 - 20 * f
            elif t < 6:
                x, y = 442, 95
            elif t < 9:
                f = (t - 6) / 3
                x, y = 442 + 148 * f, 95 + 85 * f - 160 * f * (1 - f)
            else:
                x, y = 590, 195
            circle(frame, x, y, 7, (255, 161, 72))
            process.stdin.write(frame)
        process.stdin.close()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if process.wait() != 0:
        raise SystemExit("FFmpeg render failed")
    print(OUTPUT)


if __name__ == "__main__":
    main()
