const TEAM_COLOR = { HOU: "#CE1141", DAL: "#0064B1" };
const PX = 4.4;
const BASKET_Y = 5.25;

const els = {
  subhead: document.querySelector("#subhead"),
  stage: document.querySelector("#stage"),
  reel: document.querySelector("#reel"),
  names: document.querySelector("#names"),
  court: document.querySelector("#court"),
  detail: document.querySelector("#detail"),
  script: document.querySelector("#script"),
  events: document.querySelector("#events"),
  fill: document.querySelector("#fill"),
  ticks: document.querySelector("#ticks"),
  scrub: document.querySelector("#scrub"),
  clock: document.querySelector("#clock"),
  pause: document.querySelector("#pause"),
  pauseIcon: document.querySelector("#pause-icon"),
  switcher: document.querySelector("#clip-switch"),
  foot: document.querySelector("#foot-note"),
};

const params = new URLSearchParams(location.search);
const jobId = params.get("jobId");
const clips = window.DEEPCOURT_CLIPS || {};
const clipName = params.get("clip") || (clips.flagg ? "flagg" : Object.keys(clips)[0] || "");

const state = {
  pack: null,
  events: [],
  active: 0,
  clipDuration: 1,
  paused: true,
  layers: { resident: true, event: true, expand: true },
  freeze: null,
  replayed: new Set(),
};

init();

function init() {
  renderSwitcher();
  els.reel.addEventListener("timeupdate", onTimeUpdate);
  els.reel.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(els.reel.duration) && els.reel.duration > 0) state.clipDuration = els.reel.duration;
    resizeCanvas();
    if (state.pack && els.reel.currentTime < 0.2) seekEvent(state.active);
    else render();
  });
  els.reel.addEventListener("ended", () => {
    state.replayed.clear();
    pause(true);
    state.active = Math.max(0, state.events.length - 1);
    render();
  });
  els.reel.addEventListener("seeked", () => render());
  window.addEventListener("resize", resizeCanvas);
  els.scrub.addEventListener("click", onScrub);
  els.pause.addEventListener("click", () => { pause(!state.paused); render(); });
  els.events.addEventListener("click", (event) => {
    const button = event.target.closest(".event");
    if (!button) return;
    seekEvent(Number(button.dataset.index));
  });
  const kuaishou = document.querySelector("#share-kuaishou");
  if (kuaishou) kuaishou.addEventListener("click", () => openKuaishou());
  document.querySelectorAll(".layer").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.layer;
      state.layers[key] = !state.layers[key];
      if (key === "expand") pause(state.layers.expand);
      render();
    });
  });
  if (clipName && clips[clipName]) loadPack(clips[clipName]);
  else if (jobId) pollJob();
  else els.subhead.textContent = "还没有成片。先在仓库里运行 python3 -m pipeline.present --all";
}

function loadPack(pack) {
  endFreeze(false);
  state.replayed.clear();
  state.pack = pack;
  state.events = pack.events || [];
  state.clipDuration = pack.duration || 1;
  state.active = nearestEventIndex(pack.anchor || 0);
  els.subhead.innerHTML = `${pack.dataset ? `<span class="dataset">${pack.dataset}</span>` : ""}Team 04 · ${pack.title || pack.clip}${pack.gameId ? " · 官方 " + pack.gameId : ""}`;
  const start = Number(pack.anchor) || 0;
  els.reel.src = start > 0 ? `${pack.video}#t=${start}` : pack.video;
  if (els.foot) {
    els.foot.textContent = pack.gameId
      ? `官方比赛 ${pack.gameId}。${pack.voice || ""}`
      : (pack.voice || "画面上的数字来自记分牌和官方逐回合。");
    if (pack.replayVideo) {
      const link = document.createElement("a");
      link.href = pack.replayVideo;
      link.download = "";
      link.className = "replay-link";
      link.textContent = "下载定格版成片（进球后出手帧定格 5 秒）";
      els.foot.append(" ", link);
    }
  }
  if (els.script) els.script.textContent = (pack.script || []).join("");
  drawCourt();
  renderTicks();
  render();
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

function resizeCanvas() {
  const canvas = els.names;
  if (!canvas || !els.stage) return;
  const rect = els.stage.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  canvas.style.width = `${rect.width}px`;
  canvas.style.height = `${rect.height}px`;
  drawNames();
}

function startFreeze(shot) {
  const freeze = window.DeepFreeze;
  state.replayed.add(shot.t);
  state.freeze = { shot, resumeAt: els.reel.currentTime, until: Date.now() + freeze.FREEZE_MS };
  els.reel.pause();
  els.reel.currentTime = shot.release.t;
  let card = document.querySelector("#freeze");
  if (!card) {
    card = document.createElement("div");
    card.id = "freeze";
    card.className = "freeze";
    els.stage.appendChild(card);
  }
  card.hidden = false;
  const [x1, , x2] = shot.release.shooter_box;
  card.classList.toggle("right", (x1 + x2) / 2 < state.pack.width / 2);
  card.style.fontSize = `${Math.max(8.5, Math.min(12, els.stage.clientWidth / 62)).toFixed(1)}px`;
  const tick = () => {
    if (!state.freeze || state.freeze.shot !== shot) return;
    const left = state.freeze.until - Date.now();
    if (left <= 0) {
      endFreeze(true);
      return;
    }
    card.innerHTML = freeze.cardHtml(state.pack, shot, left);
    state.freeze.timer = setTimeout(tick, 200);
  };
  tick();
  drawCourt();
}

function endFreeze(resume) {
  const current = state.freeze;
  if (!current) return;
  clearTimeout(current.timer);
  state.freeze = null;
  const card = document.querySelector("#freeze");
  if (card) card.hidden = true;
  if (resume) {
    els.reel.currentTime = current.resumeAt;
    if (!state.paused) els.reel.play().catch(() => {});
  }
  render();
}

function maybeFreeze() {
  if (!window.DeepFreeze || !state.pack || state.paused || state.freeze) return false;
  const t = els.reel.currentTime;
  const shot = window.DeepFreeze.candidates(state.pack).find((item) => !state.replayed.has(item.t)
    && t >= window.DeepFreeze.goalTime(state.pack, item) && t <= window.DeepFreeze.goalTime(state.pack, item) + 1.5);
  if (!shot) return false;
  startFreeze(shot);
  return true;
}

function rearmFreeze(t) {
  endFreeze(false);
  (state.pack?.shots || []).forEach((shot) => {
    if (shot.release && t < window.DeepFreeze?.goalTime(state.pack, shot)) state.replayed.delete(shot.t);
  });
}

function pause(shouldPause) {
  state.paused = shouldPause;
  state.layers.expand = shouldPause;
  if (state.freeze) {
    endFreeze(!shouldPause);
    return;
  }
  if (!els.reel.src) return;
  if (state.paused) els.reel.pause();
  else els.reel.play().catch(() => { state.paused = true; state.layers.expand = true; });
}

function seekEvent(index) {
  state.active = index;
  pause(true);
  const event = state.events[index];
  if (event) {
    rearmFreeze(event.t);
    const duration = els.reel.duration;
    const limit = Number.isFinite(duration) && duration > 0.2 ? duration - 0.05 : event.t;
    try {
      els.reel.currentTime = Math.min(event.t, Math.max(0, limit));
    } catch (error) {
      els.reel.addEventListener("loadedmetadata", () => {
        els.reel.currentTime = event.t;
      }, { once: true });
    }
  }
  render();
}

function onScrub(event) {
  const rect = els.scrub.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  els.reel.currentTime = ratio * state.clipDuration;
  state.active = nearestEventIndex(els.reel.currentTime);
  pause(true);
  rearmFreeze(els.reel.currentTime);
  render();
}

function onTimeUpdate() {
  drawNames();
  if (state.paused || state.freeze || maybeFreeze()) return;
  const next = nearestEventIndex(els.reel.currentTime);
  if (next !== state.active) state.active = next;
  render();
}

function nearestEventIndex(t) {
  let active = 0;
  state.events.forEach((item, index) => { if (t + 0.05 >= item.t) active = index; });
  return active;
}

function scoreAt(t) {
  const rows = state.pack?.scorebug || [];
  let found = rows[0] || null;
  rows.forEach((row) => { if (row.t <= t + 0.05) found = row; });
  return found;
}

function render() {
  const t = Number.isFinite(els.reel.currentTime) ? els.reel.currentTime : 0;
  els.fill.style.width = `${Math.min(100, (t / state.clipDuration) * 100)}%`;
  const bug = scoreAt(t);
  els.clock.textContent = bug ? `${bug.period} ${bug.clock}` : format(t);
  els.pauseIcon.innerHTML = state.paused
    ? '<rect x="1" y="0" width="3.2" height="14" fill="#14110E"/><rect x="7.6" y="0" width="3.2" height="14" fill="#14110E"/>'
    : '<path d="M2 0 L12 7 L2 14 Z" fill="#14110E"/>';
  els.names.classList.toggle("hidden", !state.layers.resident);
  els.events.innerHTML = state.events.map((item, index) => {
    const on = index === state.active ? " on" : "";
    return `<button class="event${on}" type="button" data-index="${index}"><span class="t">${item.timecode || "--:--"}</span><span class="l">${item.label}</span></button>`;
  }).join("");
  document.querySelectorAll(".layer").forEach((button) => {
    button.classList.toggle("on", state.layers[button.dataset.layer]);
  });
  drawNames();
  drawDetail();
  drawCourt();
}

function drawNames() {
  const canvas = els.names;
  const pack = state.pack;
  if (!canvas || !pack || !state.layers.resident) {
    if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const frame = nearestFrame(els.reel.currentTime || 0);
  if (!frame) return;
  const ratio = window.devicePixelRatio || 1;
  const scale = canvas.width / pack.width;
  if (state.freeze && window.DeepFreeze) {
    window.DeepFreeze.drawMarks(ctx, state.freeze.shot, scale, ratio);
    return;
  }
  ctx.font = `${Math.round(13 * ratio)}px MiSans, "PingFang SC", sans-serif`;
  ctx.textBaseline = "bottom";
  const t = els.reel.currentTime || 0;
  const shotOn = (pack.shots || []).some((shot) => t >= shot.t - 0.3 && t <= shot.t + 3.3);
  frame.players.forEach((player) => {
    if (shotOn && player.y < 360) return;
    const label = `${player.name} ${player.jersey}`.trim();
    const width = ctx.measureText(label).width + 12 * ratio;
    const x = player.x * scale + (player.w * scale - width) / 2;
    const y = player.y * scale - 4 * ratio;
    const top = Math.max(16 * ratio, y);
    ctx.fillStyle = "rgba(20,17,14,0.88)";
    ctx.fillRect(x, top - 18 * ratio, width, 18 * ratio);
    ctx.fillStyle = TEAM_COLOR[player.team] || "#F1681E";
    ctx.fillRect(x, top - 18 * ratio, 3 * ratio, 18 * ratio);
    ctx.fillStyle = "#F3EDE2";
    ctx.fillText(label, x + 7 * ratio, top - 3 * ratio);
  });
}

function nearestFrame(t) {
  const tags = state.pack?.tags || [];
  let found = null;
  for (const frame of tags) {
    if (frame.t <= t + 0.12) found = frame;
    else break;
  }
  return found;
}

function drawDetail() {
  if (!els.detail) return;
  const event = state.events[state.active];
  const shot = event?.shot || null;
  if (!state.layers.expand || !state.paused) {
    els.detail.innerHTML = shot
      ? `<p>${shot.name} · ${shot.made ? "命中" : shot.made === false ? "不中" : "出手"}</p><p class="muted">暂停后展开官方记录。</p>`
      : "<p class='muted'>暂停后展开这一段的官方记录。</p>";
    return;
  }
  if (!shot) {
    els.detail.innerHTML = `<p>${event ? event.label : "这一段没有出手。"}</p><p class="muted">${event?.detail || ""}</p>${lineupHtml()}`;
    return;
  }
  const xfg = shot.xfg == null ? "清单里没有预期命中率" : `预期命中率 ${(Number(shot.xfg) * 100).toFixed(1)}%（${Number(shot.xfg).toFixed(3)}）`;
  const margin = shot.season_margin;
  const vsTeam = margin && margin.ts != null && margin.team_ts != null
    ? (Number(margin.ts) - Number(margin.team_ts)) * 100
    : null;
  const vsTeamText = vsTeam == null ? ""
    : `，比全队同档${vsTeam >= 0 ? "高" : "低"} ${Math.abs(vsTeam).toFixed(1)} 个百分点`;
  const marginLine = margin && margin.ts != null
    ? `<p>赛季${margin.bucket_zh}：真实命中率 ${(Number(margin.ts) * 100).toFixed(1)}%${vsTeamText}，每球权 ${Number(margin.ppp).toFixed(3)}（${margin.fga} 投）</p>`
    : "";
  const bits = [shot.zone, shot.distance ? `${Number(shot.distance).toFixed(1)} 英尺` : "", shot.assist ? `${shot.assist} 助攻` : ""].filter(Boolean);
  els.detail.innerHTML = `<p><b>${shot.name}</b> ${shot.value || ""} 分${shot.made ? "命中" : shot.made === false ? "不中" : "出手"}</p>
    <p>${xfg}</p>
    ${marginLine}
    <p>${bits.join(" · ")}</p>
    <p class="muted">${shot.description || ""}</p>
    ${shot.contrast ? `<p class="contrast">${shot.contrast}</p>` : ""}
    ${lineupHtml()}`;
}

function lineupHtml() {
  const court = state.pack?.onCourt || {};
  const teams = Object.keys(court);
  if (!teams.length) return "";
  return `<div class="lineup">${teams.map((team) => `<div><b style="color:${TEAM_COLOR[team] || "#F3EDE2"}">${team === "HOU" ? "火箭" : team === "DAL" ? "独行侠" : team}</b>${
    court[team].map((player) => `<span>#${player.jersey} ${player.name}</span>`).join("")
  }</div>`).join("")}</div>`;
}

function drawCourt() {
  if (!els.court || !state.layers.event) {
    if (els.court) els.court.innerHTML = "";
    return;
  }
  const shots = state.pack?.shots || [];
  const focus = state.freeze?.shot || state.events[state.active]?.shot || shots.find((shot) => shot.zoneChart);
  const kicker = els.court.parentElement?.querySelector(".rail-kicker");
  if (window.DeepFreeze && focus?.zoneChart) {
    if (kicker) kicker.textContent = `${focus.name} · 出手分区热区图`;
    els.court.innerHTML = window.DeepFreeze.heatmap(focus, Boolean(state.freeze));
    return;
  }
  if (kicker) kicker.textContent = "半场落点 · 官方坐标";
  const w = 50 * PX;
  const h = 34 * PX;
  const sx = (x) => (25 + x) * PX;
  const sy = (y) => (BASKET_Y + y) * PX;
  const corner = Math.sqrt(23.75 ** 2 - 22 ** 2);
  const active = state.events[state.active]?.shot;
  let marks = "";
  shots.forEach((shot) => {
    if (shot.x == null || shot.y == null) return;
    const color = TEAM_COLOR[shot.team] || "#F3EDE2";
    const here = active && Math.abs(active.t - shot.t) < 0.05;
    const radius = here ? 8 : 5;
    if (shot.made) {
      marks += `<circle cx="${sx(shot.x)}" cy="${sy(shot.y)}" r="${radius}" fill="${color}" stroke="#fff" stroke-width="2"/>`;
    } else {
      const d = radius;
      marks += `<path d="M ${sx(shot.x) - d} ${sy(shot.y) - d} L ${sx(shot.x) + d} ${sy(shot.y) + d} M ${sx(shot.x) + d} ${sy(shot.y) - d} L ${sx(shot.x) - d} ${sy(shot.y) + d}" stroke="${color}" stroke-width="3"/>`;
    }
  });
  els.court.innerHTML = `<svg viewBox="0 0 ${w} ${h}" class="mini-court">
    <rect width="${w}" height="${h}" fill="#2a241c"/>
    <rect x="${sx(-8)}" y="0" width="${16 * PX}" height="${19 * PX}" fill="none" stroke="#5c5146"/>
    <circle cx="${sx(0)}" cy="${19 * PX}" r="${6 * PX}" fill="none" stroke="#5c5146"/>
    <line x1="${sx(-22)}" y1="0" x2="${sx(-22)}" y2="${sy(corner)}" stroke="#5c5146"/>
    <line x1="${sx(22)}" y1="0" x2="${sx(22)}" y2="${sy(corner)}" stroke="#5c5146"/>
    <path d="M ${sx(-22)} ${sy(corner)} A ${23.75 * PX} ${23.75 * PX} 0 0 0 ${sx(22)} ${sy(corner)}" fill="none" stroke="#5c5146"/>
    <circle cx="${sx(0)}" cy="${sy(0)}" r="3" fill="none" stroke="#F1681E"/>
    ${marks}
  </svg>`;
}

function renderTicks() {
  els.ticks.innerHTML = state.events.map((item) => {
    const pos = Math.min(100, Math.max(0, (item.t / state.clipDuration) * 100));
    return `<i style="left:${pos}%"></i>`;
  }).join("");
}

function format(total) {
  const seconds = Math.max(0, Math.round(total));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

async function pollJob() {
  els.subhead.textContent = "任务读取中";
  let attempts = 0;
  while (attempts < 60) {
    attempts += 1;
    try {
      const job = await getJson(`/jobs/${encodeURIComponent(jobId)}`);
      els.subhead.textContent = `${statusText(job.status)} · ${job.gameMeta || job.filename || jobId}`;
      if (job.playbackUrl && els.reel.src !== job.playbackUrl) els.reel.src = job.playbackUrl;
      if (job.status === "DONE" || job.status === "FAILED") {
        const listed = await getJson(`/jobs/${encodeURIComponent(jobId)}/events`);
        state.events = (listed.events || []).map((event) => ({
          t: Number.isFinite(event.t) ? event.t : 0,
          timecode: event.timecode || "",
          label: event.label || event.eventType || "事件",
          eventType: event.eventType || "",
          detail: event.detail || "",
          shot: null,
        }));
        state.clipDuration = Math.max(els.reel.duration || 0, ...state.events.map((item) => item.t), 1);
        state.active = Math.max(0, state.events.length - 1);
        renderTicks();
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

async function openKuaishou() {
  const frame = document.querySelector("#kuaishou-frame");
  try {
    const response = await fetch(`${apiBase()}/oauth/kuaishou/start`);
    const data = await response.json();
    if (data.url && frame) {
      frame.hidden = false;
      frame.src = data.url;
    }
  } catch {
    // 扫码入口没接上时，按钮保持原样。
  }
}
