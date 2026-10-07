const SAMPLE = {
  duration: 100,
  live: "● LIVE · Q4 01:40",
  active: 3,
  events: [
    { timecode: "01:22", label: "高位持球 · GRAV 87", eventType: "hold" },
    { timecode: "01:19", label: "挡拆 · 双人收缩", eventType: "screen" },
    { timecode: "01:16", label: "分球底角 · 空位 2.1m", eventType: "pass" },
    {
      timecode: "01:14",
      label: "出手 · xFG% 38%",
      eventType: "shot",
      difficulty: "62%",
      player: "弗拉格",
      grav: "87",
      xfg: "xFG% 38%",
      xfgWidth: "38%",
      detail: "DEF DIST 1.2m · 高于联盟均值 +6%",
      lvg: "LVG +1.8",
      dps: [
        { name: "阿门·汤普森", value: 0.86 },
        { name: "贾巴里·史密斯", value: 0.71 },
        { name: "申京", value: 0.42 },
      ],
      note: "主防：<b>阿门</b> · 换防指令来自申京<br>底线协防迟到 0.4s → 空位形成",
    },
    { timecode: "01:13", label: "磕筐而出 · 长篮板", eventType: "rebound" },
  ],
};

const els = {
  subhead: document.querySelector("#subhead"),
  live: document.querySelector("#live"),
  diff: document.querySelector("#diff"),
  grav: document.querySelector("#grav"),
  gravName: document.querySelector("#grav-name"),
  gravVal: document.querySelector("#grav-val"),
  zone: document.querySelector("#zone"),
  pull: document.querySelector("#pull"),
  xfg: document.querySelector("#xfg"),
  xfgVal: document.querySelector("#xfg-val"),
  xfgFill: document.querySelector("#xfg-fill"),
  xfgSub: document.querySelector("#xfg-sub"),
  dps: document.querySelector("#dps"),
  dpsRows: document.querySelector("#dps-rows"),
  dpsNote: document.querySelector("#dps-note"),
  lvg: document.querySelector("#lvg"),
  events: document.querySelector("#events"),
  fill: document.querySelector("#fill"),
  clock: document.querySelector("#clock"),
  pause: document.querySelector("#pause"),
  pauseIcon: document.querySelector("#pause-icon"),
  reel: document.querySelector("#reel"),
  court: document.querySelector("#court-art"),
};

const jobId = new URLSearchParams(location.search).get("jobId");
const state = {
  events: SAMPLE.events,
  active: SAMPLE.active,
  duration: SAMPLE.duration,
  paused: true,
  layers: { resident: true, event: true, expand: true },
  mode: jobId ? "job" : "sample",
};

render();
document.querySelectorAll(".layer").forEach((button) => {
  button.addEventListener("click", () => {
    const key = button.dataset.layer;
    state.layers[key] = !state.layers[key];
    if (key === "expand") state.paused = state.layers.expand;
    render();
  });
});
els.pause.addEventListener("click", togglePause);
els.events.addEventListener("click", (event) => {
  const button = event.target.closest(".event");
  if (!button) return;
  state.active = Number(button.dataset.index);
  const seconds = toSeconds(state.events[state.active].timecode);
  if (els.reel.src && Number.isFinite(els.reel.duration)) els.reel.currentTime = seconds;
  render();
});

if (jobId) pollJob();

function togglePause() {
  state.paused = !state.paused;
  state.layers.expand = state.paused;
  if (els.reel.src) {
    if (state.paused) els.reel.pause();
    else els.reel.play().catch(() => {});
  }
  render();
}

function render() {
  const event = state.events[state.active] || state.events[0];
  const seconds = toSeconds(event?.timecode);
  const ratio = state.duration ? Math.min(1, seconds / state.duration) : 0;
  els.fill.style.width = `${ratio * 100}%`;
  els.clock.textContent = `${event?.timecode || "00:00"} / ${format(state.duration)}`;
  els.pauseIcon.innerHTML = state.paused
    ? '<rect x="1" y="0" width="3.2" height="14" fill="#14110E"/><rect x="7.6" y="0" width="3.2" height="14" fill="#14110E"/>'
    : '<path d="M2 0 L12 7 L2 14 Z" fill="#14110E"/>';

  const showResident = state.layers.resident;
  const showEvent = state.layers.event;
  els.diff.classList.toggle("hidden", !showResident);
  els.grav.classList.toggle("hidden", !showResident);
  els.xfg.classList.toggle("hidden", !showResident);
  els.zone.classList.toggle("hidden", !showEvent);
  els.pull.classList.toggle("hidden", !showEvent);
  els.dps.classList.toggle("hidden", !state.layers.expand);

  if (event) {
    els.diff.innerHTML = `投篮难度 <b>${event.difficulty || "—"}</b>`;
    els.gravName.textContent = event.player || "球员";
    els.gravVal.textContent = event.grav || "—";
    els.xfgVal.textContent = event.xfg || event.label || event.eventType || "";
    els.xfgFill.style.width = event.xfgWidth || "38%";
    els.xfgSub.textContent = event.detail || "";
    els.lvg.textContent = event.lvg || "LVG —";
    els.dpsNote.innerHTML = event.note || "暂停后展开本回合的防守压力。";
    drawDps(event.dps);
  }

  els.events.innerHTML = state.events.map((item, index) => {
    const on = index === state.active ? " on" : "";
    return `<button class="event${on}" type="button" data-index="${index}"><span class="t">${item.timecode || "--:--"}</span><span class="l">${item.label}</span></button>`;
  }).join("");

  document.querySelectorAll(".layer").forEach((button) => {
    button.classList.toggle("on", state.layers[button.dataset.layer]);
  });
}

function drawDps(rows) {
  if (!rows || !rows.length) {
    els.dpsRows.innerHTML = "";
    return;
  }
  els.dpsRows.innerHTML = rows.map((row) => {
    const width = Math.round(Number(row.value) * 100);
    const low = Number(row.value) < 0.5;
    return `<div class="dps-row"><span>${row.name}</span><div class="bar"><i class="${low ? "low" : ""}" style="width:${width}%"></i></div><span class="num${low ? " low" : ""}">${Number(row.value).toFixed(2)}</span></div>`;
  }).join("");
}

async function pollJob() {
  els.live.textContent = "任务读取中";
  let attempts = 0;
  while (attempts < 60) {
    attempts += 1;
    try {
      const job = await getJson(`/jobs/${encodeURIComponent(jobId)}`);
      els.subhead.textContent = `${statusText(job.status)} · ${job.gameMeta || job.filename || jobId}`;
      els.live.textContent = job.modelUsed ? job.modelUsed : statusText(job.status);
      if (job.playbackUrl) {
        els.reel.src = job.playbackUrl;
        els.reel.classList.remove("hidden");
        els.court.classList.add("hidden");
      }
      if (job.status === "DONE" || job.status === "FAILED") {
        const listed = await getJson(`/jobs/${encodeURIComponent(jobId)}/events`);
        if (listed.events?.length) {
          state.events = listed.events.map(presentEvent);
          state.active = state.events.length - 1;
          state.duration = Math.max(...state.events.map((item) => toSeconds(item.timecode)), 1);
        }
        if (job.status === "FAILED") els.subhead.textContent = job.error || "分析失败";
        render();
        return;
      }
    } catch (error) {
      els.subhead.textContent = error.message;
    }
    await wait(3000);
  }
}

function presentEvent(event) {
  return {
    ...event,
    label: event.label || event.eventType || "事件",
    player: (event.players && event.players[0]) || "",
    detail: event.detail || "",
    xfg: event.label || "",
  };
}

function statusText(status) {
  return {
    PENDING: "等待分析",
    ANALYZING: "AgentCore / Bedrock 分析中",
    DONE: "分析完成",
    FAILED: "分析失败",
  }[status] || status || "未知状态";
}

function apiBase() {
  const fromQuery = new URLSearchParams(location.search).get("api");
  if (fromQuery) return fromQuery.replace(/\/$/, "");
  return (window.DEEPCOURT_CONFIG?.apiBase || "/api").replace(/\/$/, "");
}

async function getJson(path) {
  const response = await fetch(`${apiBase()}${path}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `接口返回 ${response.status}`);
  return data;
}

function toSeconds(timecode) {
  const match = /^(\d+):(\d+)$/.exec(timecode || "");
  if (!match) return 0;
  return Number(match[1]) * 60 + Number(match[2]);
}

function format(total) {
  const seconds = Math.max(0, Math.round(total));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
