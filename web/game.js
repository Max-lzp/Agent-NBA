const SAMPLE = {
  duration: 100,
  live: "● LIVE · Q4 01:40",
  active: 3,
  events: [
    { timecode: "01:22", posSec: 82, clipTime: 0.8, label: "高位持球 · GRAV 87", eventType: "hold" },
    { timecode: "01:18", posSec: 78, clipTime: 2.8, label: "挡拆 · 双人收缩", eventType: "screen" },
    { timecode: "01:16", posSec: 76, clipTime: 4.3, label: "分球底角 · 空位 2.1m", eventType: "pass" },
    {
      timecode: "01:14",
      posSec: 74,
      clipTime: 6.3,
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
    { timecode: "01:13", posSec: 73, clipTime: 8.6, label: "磕筐而出 · 长篮板", eventType: "rebound" },
  ],
};

const els = {
  subhead: document.querySelector("#subhead"),
  live: document.querySelector("#live"),
  chromeUrl: document.querySelector("#chrome-url"),
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
  ticks: document.querySelector("#ticks"),
  scrub: document.querySelector("#scrub"),
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
  clipDuration: 10,
  paused: true,
  layers: { resident: true, event: true, expand: true },
  mode: jobId ? "job" : "sample",
};

init();

function init() {
  if (els.chromeUrl) els.chromeUrl.textContent = location.host + location.pathname;
  state.events.forEach((event) => {
    if (event.posSec == null) event.posSec = toSeconds(event.timecode);
    if (event.pos == null) event.pos = event.posSec / state.duration;
  });
  els.reel.addEventListener("timeupdate", onTimeUpdate);
  els.reel.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(els.reel.duration) && els.reel.duration > 0) {
      state.clipDuration = els.reel.duration;
      if (state.mode === "job") {
        state.duration = els.reel.duration;
        state.events.forEach((event) => {
          event.posSec = event.clipTime;
          event.pos = event.clipTime / state.clipDuration;
        });
      }
      renderTicks();
      render();
    }
  });
  els.reel.addEventListener("ended", () => {
    pause(true);
    state.active = state.events.length - 1;
    render();
  });
  els.scrub.addEventListener("click", onScrub);
  els.pause.addEventListener("click", togglePause);
  els.events.addEventListener("click", (event) => {
    const button = event.target.closest(".event");
    if (!button) return;
    seekEvent(Number(button.dataset.index));
  });
  document.querySelectorAll(".layer").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.layer;
      state.layers[key] = !state.layers[key];
      if (key === "expand") pause(state.layers.expand);
      render();
    });
  });

  if (state.mode === "sample") {
    els.live.textContent = SAMPLE.live;
    els.reel.currentTime = SAMPLE.events[SAMPLE.active].clipTime;
  }
  renderTicks();
  render();
  if (jobId) pollJob();
}

function togglePause() {
  pause(!state.paused);
  render();
}

function pause(shouldPause) {
  state.paused = shouldPause;
  state.layers.expand = shouldPause;
  if (els.reel.src || state.mode === "sample") {
    if (state.paused) els.reel.pause();
    else els.reel.play().catch(() => { state.paused = true; state.layers.expand = true; render(); });
  }
}

function seekEvent(index) {
  state.active = index;
  const event = state.events[state.active];
  if (event && Number.isFinite(els.reel.duration)) {
    els.reel.currentTime = Math.min(event.clipTime, Math.max(0, els.reel.duration - 0.05));
  } else if (event) {
    els.reel.currentTime = event.clipTime;
  }
  pause(true);
  render();
}

function onScrub(event) {
  const rect = els.scrub.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  els.reel.currentTime = ratio * state.clipDuration;
  let active = 0;
  state.events.forEach((item, index) => { if (ratio >= item.pos - 0.01) active = index; });
  state.active = active;
  pause(true);
  render();
}

function onTimeUpdate() {
  if (state.paused) return;
  const t = els.reel.currentTime;
  let active = 0;
  state.events.forEach((item, index) => { if (t >= item.clipTime - 0.05) active = index; });
  if (active !== state.active) {
    state.active = active;
    render();
  } else {
    updateProgress();
  }
}

function displayPosition() {
  if (state.paused) return state.events[state.active]?.posSec ?? 0;
  const t = els.reel.currentTime || 0;
  const evs = state.events;
  let prev = { clipTime: 0, posSec: 0 };
  let next = null;
  for (const ev of evs) {
    if (ev.clipTime <= t) prev = ev;
    else { next = ev; break; }
  }
  if (!next) return evs[evs.length - 1]?.posSec ?? 0;
  const span = Math.max(0.001, next.clipTime - prev.clipTime);
  return lerp(prev.posSec, next.posSec, Math.min(1, (t - prev.clipTime) / span));
}

function updateProgress() {
  const position = displayPosition();
  els.fill.style.width = `${(position / state.duration) * 100}%`;
  els.clock.textContent = `${format(position)} / ${format(state.duration)}`;
}

function render() {
  updateProgress();
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

  const event = state.events[state.active] || state.events[0];
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

function renderTicks() {
  els.ticks.innerHTML = state.events.map((item) => {
    const pos = Math.min(100, Math.max(0, (item.pos ?? 0) * 100));
    return `<i style="left:${pos}%"></i>`;
  }).join("");
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
      if (job.playbackUrl && els.reel.src !== job.playbackUrl) {
        els.reel.src = job.playbackUrl;
        els.reel.poster = "";
        els.reel.classList.remove("hidden");
        els.court.classList.add("hidden");
      }
      if (job.status === "DONE" || job.status === "FAILED") {
        const listed = await getJson(`/jobs/${encodeURIComponent(jobId)}/events`);
        if (listed.events?.length) {
          state.events = listed.events.map(presentEvent);
          state.active = state.events.length - 1;
          const last = Math.max(...state.events.map((item) => item.clipTime), 1);
          state.duration = state.clipDuration > 1 ? state.clipDuration : last;
          state.clipDuration = Math.max(state.clipDuration, last);
          state.events.forEach((item) => {
            item.posSec = item.clipTime;
            item.pos = item.clipTime / state.clipDuration;
          });
          renderTicks();
          seekEvent(state.active);
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
    clipTime: toSeconds(event.timecode),
    pos: 0,
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

function lerp(a, b, t) { return a + (b - a) * t; }

function format(total) {
  const seconds = Math.max(0, Math.round(total));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
