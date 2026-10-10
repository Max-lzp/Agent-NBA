const els = {
  subhead: document.querySelector("#subhead"),
  reel: document.querySelector("#reel"),
  switcher: document.querySelector("#clip-switch"),
  foot: document.querySelector("#foot-note"),
};

const params = new URLSearchParams(location.search);
const jobId = params.get("jobId");
const clips = window.DEEPCOURT_CLIPS || {};
const clipName = params.get("clip") || (clips.flagg ? "flagg" : Object.keys(clips)[0] || "");

init();

function init() {
  renderSwitcher();
  if (clipName && clips[clipName]) loadPack(clips[clipName]);
  else if (jobId) pollJob();
  else els.subhead.textContent = "还没有成片。先在仓库里运行 python3 -m pipeline.present --all";
}

function loadPack(pack) {
  const replay = pack.replayVideo || "";
  const broadcast = pack.video || "";
  const kind = replay ? "定格回放成片" : "转播成片";
  els.subhead.innerHTML = `${pack.dataset ? `<span class="dataset">${pack.dataset}</span>` : ""}Team 04 · ${pack.title || pack.clip}${pack.gameId ? " · 官方 " + pack.gameId : ""} · ${kind}`;
  play(replay || broadcast, replay ? broadcast : "");
  if (els.foot) {
    els.foot.textContent = pack.gameId
      ? `官方比赛 ${pack.gameId}。${pack.voice || ""}`
      : (pack.voice || "画面上的数字来自记分牌和官方逐回合。");
  }
}

function play(src, fallback) {
  if (!src) return;
  els.reel.onerror = () => {
    if (!fallback || els.reel.getAttribute("src") === fallback) return;
    els.reel.onerror = null;
    els.reel.src = fallback;
    els.subhead.insertAdjacentText("beforeend", "（回放文件打不开，改播转播成片）");
  };
  els.reel.src = src;
}

function renderSwitcher() {
  if (!els.switcher) return;
  const names = Object.keys(clips);
  els.switcher.innerHTML = names.map((name) => {
    const on = name === clipName ? " on" : "";
    const label = `${clips[name].dataset ? clips[name].dataset + " · " : ""}${clips[name].title || name}`;
    return `<a class="clip${on}" href="game.html?clip=${encodeURIComponent(name)}">${label}</a>`;
  }).join("");
}

async function pollJob() {
  els.subhead.textContent = "任务读取中";
  let attempts = 0;
  while (attempts < 60) {
    attempts += 1;
    try {
      const job = await getJson(`/jobs/${encodeURIComponent(jobId)}`);
      els.subhead.textContent = `${statusText(job.status)} · ${job.gameMeta || job.filename || jobId}`;
      if (job.playbackUrl && els.reel.getAttribute("src") !== job.playbackUrl) els.reel.src = job.playbackUrl;
      if (job.status === "DONE" || job.status === "FAILED") {
        if (job.status === "FAILED") els.subhead.textContent = job.error || "分析失败";
        if (els.foot) els.foot.textContent = "成片由任务产出。播放、暂停和进度用系统控件。";
        return;
      }
    } catch (error) {
      els.subhead.textContent = error.message;
    }
    await wait(3000);
  }
}

function statusText(status) {
  return {
    PENDING: "等待上传",
    TRACKING: "等待本地追踪结果",
    ANALYZING: "AgentCore 校验结构化清单",
    DONE: "分析完成",
    FAILED: "分析失败",
  }[status] || status || "未知状态";
}

function apiBase() {
  const fromQuery = params.get("api");
  if (fromQuery) return fromQuery.replace(/\/$/, "");
  return (window.DEEPCOURT_CONFIG?.apiBase || "/api").replace(/\/$/, "");
}

async function getJson(path) {
  const response = await fetch(`${apiBase()}${path}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `接口返回 ${response.status}`);
  return data;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
