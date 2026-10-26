"use strict";
(async function () {
  const list = document.getElementById("official-list"), video = document.getElementById("official-video"), detail = document.getElementById("official-detail"), status = document.getElementById("official-status");
  let selected = null;
  const node = (tag, value, className) => { const item = document.createElement(tag); item.textContent = value; if (className) item.className = className; return item; };
  const safeURL = (value, hosts) => { const url = new URL(value); if (url.protocol !== "https:" || !hosts.includes(url.hostname) || url.username || url.password) throw new Error("来源地址不在官方域名范围"); return url.href; };
  function select(clip, button) {
    video.pause(); selected = clip;
    list.querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    detail.replaceChildren(node("h2", `${clip.playerZh} · 第 ${clip.period} 节 ${clip.clock}`));
    detail.append(node("p", `${clip.shotValue} 分出手${clip.made ? "命中" : "未中"} · ${clip.action}`));
    detail.append(node("p", `预期命中率 ${(clip.shotQuality * 100).toFixed(1)}% · 简化预期得分 ${clip.expectedPoints.toFixed(2)} 分 · 实际 ${clip.actualPoints} 分`));
    detail.append(node("p", `比赛 ${clip.gameId} / 事件 ${clip.eventId} · 官方记录时长 ${clip.expectedDuration.toFixed(2)} 秒`, "muted"));
    for (const [label, url, hosts] of [["NBA 官方事件页", clip.officialPage, ["www.nba.com"]], ["官方视频原链接", clip.videoUrl, ["videos.nba.com"]], ["统计数据来源", clip.sourceUrl, ["stats.nba.com"]]]) {
      const link = node("a", label); link.href = safeURL(url, hosts); link.target = "_blank"; link.rel = "noopener noreferrer"; detail.append(link);
    }
    detail.append(node("p", `来源快照采集于 ${clip.fetchedAt.slice(0, 10)}。快照哈希已核对；尚未核实当前视频内容与具体出手帧。`, "muted"));
    status.textContent = "正在连接 NBA 官方视频源…";
    video.src = safeURL(clip.videoUrl, ["videos.nba.com"]); video.load();
  }
  video.addEventListener("loadedmetadata", () => {
    if (!selected) return;
    if (!Number.isFinite(video.duration) || Math.abs(video.duration - selected.expectedDuration) > .6) {
      video.pause(); video.removeAttribute("src"); video.load();
      status.textContent = "视频时长与官方记录不符，已停止加载，避免播放占位或错误素材。请打开官方事件页核对。";
      return;
    }
    status.textContent = "视频已解码，时长与来源记录一致。请点击播放核对画面；这不等同于已验证出手时间轴。";
  });
  video.addEventListener("error", () => { if (selected) status.textContent = "NBA 源站暂时无法播放，可能受网络或源站策略限制。可以打开官方事件页查看；当前不会替换为其他片段。"; });
  try {
    const response = await fetch("replay/official-clips.json");
    if (!response.ok) throw new Error("样本目录暂时读不到");
    const data = await response.json();
    if (data.schemaVersion !== 1 || !Array.isArray(data.clips) || data.clips.length > 20) throw new Error("样本目录格式无效");
    for (const clip of data.clips) {
      safeURL(clip.videoUrl, ["videos.nba.com"]);
      const card = node("article", "", "official-card");
      card.append(node("h2", `${clip.playerZh} · ${clip.shotValue} 分${clip.made ? "命中" : "未中"}`), node("p", `第 ${clip.period} 节 ${clip.clock} · 预期命中率 ${(clip.shotQuality * 100).toFixed(1)}%`));
      const button = node("button", "加载官方片段"); button.type = "button"; button.setAttribute("aria-pressed", "false"); button.addEventListener("click", () => select(clip, button)); card.append(button); list.append(card);
    }
    status.textContent = `已封装 ${data.clips.length} 个官方来源样本。选择片段后才连接 NBA 视频源。`;
  } catch (error) { status.textContent = error.message; }
})();
