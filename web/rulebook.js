"use strict";
(async function () {
  const video = document.getElementById("rule-video"), list = document.getElementById("rule-list"), detail = document.getElementById("rule-detail"), status = document.getElementById("rule-status");
  let current = null, mode = "new";
  const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
  const url = (value, host) => { const parsed = new URL(value); if (parsed.protocol !== "https:" || parsed.hostname !== host || parsed.username || parsed.password) throw new Error("官方来源地址无效"); return parsed.href; };
  function describe() {
    if (!current) return;
    detail.replaceChildren(text("h2", current.title), text("p", current.notes[mode]));
    detail.append(text("p", "NBA 官方判例：" + current.officialTitle, "muted"));
    const link = text("a", "查看 NBA 官方判例与原视频 ↗"); link.href = url(current.officialPage, "videorulebook.nba.com"); link.target = "_blank"; link.rel = "noopener noreferrer"; detail.append(link);
    detail.append(text("p", `已核对原视频：${current.duration.toFixed(2)} 秒。中文内容为依据官方解释整理的观看提示。`, "muted"));
  }
  document.querySelectorAll("#rule-modes button").forEach((button) => button.addEventListener("click", () => {
    mode = button.dataset.mode;
    document.querySelectorAll("#rule-modes button").forEach(item => { item.setAttribute("aria-pressed", String(item === button)); item.classList.toggle("on", item === button); }); describe();
  }));
  video.addEventListener("loadedmetadata", () => {
    if (!current) return;
    if (!Number.isFinite(video.duration) || Math.abs(video.duration - current.duration) > .6) {
      video.pause(); video.removeAttribute("src"); video.load(); status.textContent = "返回视频与已核对时长不一致，已停止加载。请打开官方判例页。";
    } else status.textContent = "官方片段已就绪，可点击播放；可暂停观察动作，并切换中文解释。";
  });
  video.addEventListener("error", () => { status.textContent = "当前网络无法播放 NBA 原站视频。请打开下方官方判例页查看，或稍后重试。"; });
  try {
    const response = await fetch("replay/official-rulebook.json"); if (!response.ok) throw new Error("官方教学目录暂时读不到");
    const data = await response.json(); if (data.schemaVersion !== 1 || !Array.isArray(data.clips) || data.clips.length > 20) throw new Error("目录格式无效");
    for (const clip of data.clips) {
      url(clip.videoUrl, "ak-static.cms.nba.com"); url(clip.officialPage, "videorulebook.nba.com");
      const card = text("article", "", "official-card"); card.append(text("h2", clip.title), text("p", `NBA 官方规则示例 · ${clip.duration.toFixed(1)} 秒 · 来源与媒体已核对`));
      const button = text("button", "观看官方片段"); button.type = "button"; button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        video.pause(); current = clip; describe(); list.querySelectorAll("button").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
        status.textContent = "正在加载 NBA 官方视频…"; video.src = url(clip.videoUrl, "ak-static.cms.nba.com"); video.load();
      }); card.append(button); list.append(card);
    }
    document.querySelector('#rule-modes [data-mode="new"]').classList.add("on");
    status.textContent = "两段官方示例已准备好，选择一段开始观看。视频仅在点击后加载。";
  } catch (error) { status.textContent = error.message; }
})();
