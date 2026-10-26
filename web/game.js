const TEAM_COLOR = { HOU: "#CE1141", DAL: "#0064B1" };
const TEAM_MARK = {
  DAL: {
    name: "独行侠",
    jersey: "深色衣",
    swatch: "dark",
    logo: "logos/1610612742.svg?v=1",
  },
  HOU: {
    name: "火箭",
    jersey: "白衣",
    swatch: "white",
    logo: "logos/1610612745.svg?v=1",
  },
};
const HEADSHOTS = {
  "DAL:00": 1631108,
  "DAL:2": 1641726,
  "DAL:3": 203076,
  "DAL:7": 203939,
  "DAL:8": 1642358,
  "DAL:9": 1642948,
  "DAL:10": 1630314,
  "DAL:11": 202681,
  "DAL:13": 1630230,
  "DAL:16": 1628997,
  "DAL:20": 203114,
  "DAL:21": 1629655,
  "DAL:25": 1629023,
  "DAL:30": 1630619,
  "DAL:31": 202691,
  "DAL:32": 1642843,
  "DAL:35": 1628963,
  "HOU:0": 1628988,
  "HOU:1": 1641708,
  "HOU:2": 1627827,
  "HOU:4": 1631120,
  "HOU:5": 1627832,
  "HOU:7": 201142,
  "HOU:8": 1630256,
  "HOU:10": 1631095,
  "HOU:12": 203500,
  "HOU:13": 1641803,
  "HOU:15": 1642263,
  "HOU:17": 1631106,
  "HOU:20": 1629006,
  "HOU:27": 1642384,
  "HOU:28": 1630578,
  "HOU:30": 203991,
  "HOU:32": 201145,
};
const TEAM_LINKS = {
  DAL: {
    ics: "schedules/dal-2026-27.ics",
    store: "https://store.nba.com/dallas-mavericks/t-25921725+z-9808307-1099833793",
  },
  HOU: {
    ics: "schedules/hou-2026-27.ics",
    store: "https://store.nba.com/houston-rockets/t-25701720+z-9900621-3740132585",
  },
};
const PLAYER_INFO = {
  "DAL:00": { position: "后卫", stars: 0, titles: 0 },
  "DAL:3": { position: "前锋", stars: 10, titles: 1 },
  "DAL:13": { position: "前锋", stars: 0, titles: 0 },
  "DAL:21": { position: "前锋/中锋", stars: 0, titles: 0 },
  "DAL:31": { position: "后卫", stars: 5, titles: 4 },
  "DAL:32": { position: "前锋", stars: 0, titles: 0 },
  "HOU:1": { position: "后卫/前锋", stars: 0, titles: 0 },
  "HOU:7": { position: "前锋", stars: 16, titles: 2 },
  "HOU:10": { position: "前锋", stars: 0, titles: 0 },
  "HOU:17": { position: "前锋", stars: 0, titles: 0 },
  "HOU:28": { position: "中锋", stars: 2, titles: 0 },
  "HOU:30": { position: "中锋", stars: 0, titles: 0 },
};
const WATCH_LINKS = {
  league: "https://www.nba.com/league-pass-purchase",
  tencent: "https://v.qq.com/channel/nba",
};
const PX = 4.4;
const BASKET_Y = 5.25;

const els = {
  subhead: document.querySelector("#subhead"),
  stage: document.querySelector("#stage"),
  reel: document.querySelector("#reel"),
  names: document.querySelector("#names"),
  court: document.querySelector("#court"),
  detail: document.querySelector("#detail"),
  events: document.querySelector("#events"),
  switcher: document.querySelector("#clip-switch"),
  foot: document.querySelector("#foot-note"),
};

const params = new URLSearchParams(location.search);
const jobId = window.DEEPCOURT_CONFIG?.offline ? null : params.get("jobId");
const clips = window.DEEPCOURT_CLIPS || {};
const CFG = window.DEEPCOURT_CONFIG || {};
const EXAM_ONLY = CFG.examOnly !== false;
const SAMPLE_CLIPS = CFG.sampleClips || ["flagg", "amen", "warm-probe", "rehearsal"];
const HIDDEN_CLIPS = window.DEEPCOURT_HIDDEN || CFG.hiddenClips || [];
// 暂时不展示的音轨（例如中文解说未定版时），按 config.js 的 hiddenTracks
const HIDDEN_TRACKS = CFG.hiddenTracks || [];
const MODES = ["laofan", "xinfan", "daikan"];

// 训练片/样片：显式名单或片子 dataset 标注
function isSampleClip(name) {
  if (!name) return false;
  if (SAMPLE_CLIPS.indexOf(name) >= 0) return true;
  return Boolean(clips[name] && clips[name].dataset === "训练数据");
}

// 从切换列表里去掉的片子：显式 DEEPCOURT_HIDDEN、片子 hidden:true，或 examOnly 下的训练片
function isHiddenClip(name) {
  if (!name) return false;
  if (HIDDEN_CLIPS.indexOf(name) >= 0) return true;
  if (clips[name] && clips[name].hidden) return true;
  if (EXAM_ONLY && isSampleClip(name)) return true;
  return false;
}

function visibleClipNames() {
  return Object.keys(clips).filter((name) => !isHiddenClip(name));
}

const requestedClip = params.get("clip");
// 主片由 DEEPCOURT_LEAD 决定；主片被隐藏或不存在时退回第一支可见片。?clip= 直达任意片（含隐藏片）
const clipName = (function resolveClip() {
  if (requestedClip && clips[requestedClip]) return requestedClip;
  const lead = window.DEEPCOURT_LEAD || "";
  if (lead && clips[lead] && !isHiddenClip(lead)) return lead;
  return visibleClipNames()[0] || "";
})();

const state = {
  pack: null,
  events: [],
  active: 0,
  clipDuration: 1,
  paused: true,
  layers: { resident: true, event: true, expand: true },
  freeze: null,
  replayed: new Set(),
  watch: initialMode(),
  modePicked: MODES.indexOf(params.get("mode")) >= 0,
  lang: initialLang(),
  switchingTrack: null,
  currentSrc: "",
};
let reelRequest = 0;
let reelObjectURL = null;

function initialMode() {
  const q = params.get("mode");
  return MODES.indexOf(q) >= 0 ? q : "laofan";
}

init();

function init() {
  renderSwitcher();
  els.reel.addEventListener("timeupdate", onTimeUpdate);
  els.reel.addEventListener("loadedmetadata", () => {
    if (Number.isFinite(els.reel.duration) && els.reel.duration > 0) state.clipDuration = els.reel.duration;
    resizeCanvas();
    if (state.switchingTrack) {
      const job = state.switchingTrack;
      const closeEnough = Math.abs((els.reel.currentTime || 0) - job.t) < 0.08;
      if (closeEnough) {
        state.switchingTrack = null;
        if (job.playing) els.reel.play().catch(() => {});
        render();
      } else {
        try { els.reel.currentTime = job.t; } catch (error) { state.switchingTrack = null; }
      }
      return;
    }
    if (state.pack && els.reel.currentTime < 0.2) seekEvent(state.active);
    else render();
  });
  els.reel.addEventListener("ended", () => {
    state.replayed.clear();
    pause(true);
    state.active = Math.max(0, state.events.length - 1);
    render();
  });
  els.reel.addEventListener("play", () => {
    if (state.freeze) return;
    state.paused = false;
    state.layers.expand = false;
    render();
  });
  els.reel.addEventListener("pause", () => {
    if (state.freeze) return;
    state.paused = true;
    state.layers.expand = true;
    render();
  });
  els.reel.addEventListener("seeked", () => {
    if (state.switchingTrack) {
      const job = state.switchingTrack;
      state.switchingTrack = null;
      if (job.playing) els.reel.play().catch(() => {});
      render();
      return;
    }
    if (state.freeze) return;
    rearmFreeze(els.reel.currentTime);
    state.active = nearestEventIndex(els.reel.currentTime);
    render();
  });
  els.reel.addEventListener("canplay", () => setStageStatus(null));
  els.reel.addEventListener("playing", () => setStageStatus(null));
  els.reel.addEventListener("error", () => { if (state.currentSrc) setStageStatus("error"); });
  if (els.stage) els.stage.addEventListener("click", (event) => {
    if (event.target.closest("#stage-retry")) retryVideo();
  });
  window.addEventListener("resize", resizeCanvas);
  if (els.events) els.events.addEventListener("click", (event) => {
    const button = event.target.closest(".event");
    if (!button) return;
    seekEvent(Number(button.dataset.index));
  });
  const shareRow = document.querySelector(".share-row");
  if (shareRow) shareRow.addEventListener("click", (event) => {
    const button = event.target.closest("[data-share]");
    if (!button) return;
    openShare(button.dataset.share);
  });
  const postForm = document.querySelector("#share-post");
  if (postForm) postForm.addEventListener("submit", (event) => {
    event.preventDefault();
    publishShareDraft();
  });
  const closePost = document.querySelector("#ks-close");
  if (closePost && postForm) closePost.addEventListener("click", () => { postForm.hidden = true; });
  const watchSwitch = document.querySelector("#watch-switch");
  if (watchSwitch) {
    watchSwitch.addEventListener("click", (event) => {
      const button = event.target.closest("[data-watch]");
      if (!button) return;
      setWatch(button.dataset.watch);
    });
  }
  const langSwitch = document.querySelector("#lang-switch");
  if (langSwitch) {
    langSwitch.addEventListener("click", (event) => {
      const button = event.target.closest("[data-lang]");
      if (!button || button.disabled) return;
      setLang(button.dataset.lang);
    });
  }
  syncWatch();
  document.querySelectorAll(".layer").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.layer;
      state.layers[key] = !state.layers[key];
      if (key === "expand") pause(state.layers.expand);
      render();
    });
  });
  setupModeModal();
  if (clipName && clips[clipName]) loadPack(clips[clipName]);
  else if (jobId) pollJob();
  else if (EXAM_ONLY) showExamPending();
  else els.subhead.textContent = "还没有成片。先在仓库里运行 python3 -m pipeline.present --all";
}

function setupModeModal() {
  const modal = document.querySelector("#mode-modal");
  if (!modal) { syncWatch(); return; }
  modal.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    modal.hidden = true;
    state.modePicked = true;
    setWatch(button.dataset.mode);
  });
  syncWatch();
  if (!state.modePicked) modal.hidden = false;
}

function showExamPending() {
  if (els.subhead) els.subhead.textContent = "Team 04 · 考试片生成中";
  setStageStatus("pending");
}

function loadPack(pack) {
  endFreeze(false);
  state.replayed.clear();
  state.pack = pack;
  if (pack.clip && !isSampleClip(pack.clip)) window.DEEPCOURT_JOB_ID = pack.clip;
  state.events = pack.events || [];
  state.clipDuration = pack.duration || 1;
  state.active = nearestEventIndex(pack.anchor || 0);
  els.subhead.innerHTML = `${CFG.offline ? "个人优化作品" : "Team 04"} · ${pack.title || pack.clip}${pack.gameId ? " · 官方 " + pack.gameId : ""}`;
  const start = Number(pack.anchor) || 0;
  if (pack.tracks) HIDDEN_TRACKS.forEach((k) => { delete pack.tracks[k]; });
  if (pack.tracks && !pack.tracks[state.lang]) {
    const other = state.lang === "zh" ? "en" : "zh";
    if (pack.tracks[other]) state.lang = other;
  }
  const tracked = pack.tracks && pack.tracks[state.lang];
  setReelSrc(tracked || (start > 0 ? `${pack.video}#t=${start}` : pack.video));
  syncLang();
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
  drawCourt();
  renderTicks();
  render();
}

function renderSwitcher() {
  if (!els.switcher) return;
  const names = visibleClipNames();
  const section = document.querySelector("#more-clips");
  if (section) section.hidden = names.length <= 1;
  els.switcher.innerHTML = names.map((name) => {
    const on = name === clipName ? " on" : "";
    const label = `${clips[name].title || name}`;
    const mode = `&mode=${state.watch}`;
    const lang = state.lang === "en" ? "&lang=en" : "&lang=zh";
    return `<a class="clip${on}" href="game.html?clip=${encodeURIComponent(name)}${mode}${lang}">${label}</a>`;
  }).join("");
}

async function setReelSrc(src) {
  const request = ++reelRequest;
  state.currentSrc = src || "";
  setStageStatus(src ? "loading" : null);
  if (CFG.offline && src) {
    try {
      // Buffer the retained clip so preview servers without Range support can
      // still seek. Blob playback stays entirely on the client.
      const response = await fetch(src);
      if (!response.ok) throw new Error(`视频返回 ${response.status}`);
      const blob = await response.blob();
      if (request !== reelRequest) return;
      if (reelObjectURL) URL.revokeObjectURL(reelObjectURL);
      els.reel.src = reelObjectURL = URL.createObjectURL(blob);
    } catch (error) {
      if (request === reelRequest) setStageStatus("error");
    }
    return;
  }
  els.reel.src = src || "";
}

function setStageStatus(mode) {
  const box = document.querySelector("#stage-status");
  if (!box) return;
  if (!mode) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  if (mode === "loading") {
    box.innerHTML = `<span class="spin" aria-hidden="true"></span><p>视频加载中…</p>`;
  } else if (mode === "pending") {
    box.innerHTML = `<span class="spin" aria-hidden="true"></span><p>考试片生成中</p><p class="sub">考试片一发布，这里会自动开始播放</p>`;
  } else {
    box.innerHTML = `<p>视频加载失败</p><button type="button" id="stage-retry">重试</button>`;
  }
}

function retryVideo() {
  if (!state.currentSrc) return;
  if (CFG.offline) { setReelSrc(state.currentSrc); return; }
  setStageStatus("loading");
  if (els.reel.getAttribute("src") !== state.currentSrc) els.reel.src = state.currentSrc;
  els.reel.load();
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
  if (state.pack?.preRenderedFreeze) return false;
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
  els.names.classList.toggle("hidden", !state.layers.resident);
  renderFloor();
  if (els.events) {
    els.events.innerHTML = state.events.map((item, index) => {
      const on = index === state.active ? " on" : "";
      return `<button class="event${on}" type="button" data-index="${index}"><span class="t">${item.timecode || "--:--"}</span><span class="l">${item.label}</span></button>`;
    }).join("");
  }
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
  // 成片里已经烧进了名签；网页不再叠第二层，避免重影（config.js webNames: true 可恢复）
  if (CFG.webNames !== true) return;
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
    els.detail.innerHTML = `<p>${event ? event.label : "这一段没有出手。"}</p><p class="muted">${event?.detail || ""}</p>`;
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
  const seasonLine = seasonProfileHtml(shot);
  const bits = [shot.zone, shot.distance ? `${Number(shot.distance).toFixed(1)} 英尺` : "", shot.assist ? `${shot.assist} 助攻` : ""].filter(Boolean);
  els.detail.innerHTML = `<p><b>${shot.name}</b> ${shot.value || ""} 分${shot.made ? "命中" : shot.made === false ? "不中" : "出手"}</p>
    <p>${xfg}</p>
    ${marginLine}
    ${seasonLine}
    <p>${bits.join(" · ")}</p>
    <p class="muted">${shot.description || ""}</p>
    ${shot.contrast ? `<p class="contrast">${shot.contrast}</p>` : ""}`;
}

function seasonProfileHtml(shot) {
  const profile = shot.season_profile;
  if (!profile) return "";
  const lines = [];
  if (profile.xfg != null && profile.fg != null && profile.fg_above != null) {
    const gap = Number(profile.fg_above) * 100;
    const word = gap >= 0 ? "高于预期" : "低于预期";
    const fga = profile.tracked_fga != null ? `（跟踪 ${profile.tracked_fga} 投）` : "";
    lines.push(`赛季预期命中率 ${(Number(profile.xfg) * 100).toFixed(1)}%，实际 ${(Number(profile.fg) * 100).toFixed(1)}%，${word} ${Math.abs(gap).toFixed(1)} 个百分点${fga}`);
  }
  if (profile.quality_ppt != null) {
    const gap = Number(profile.quality_ppt);
    lines.push(`出手质量比样本基准${gap >= 0 ? "高" : "低"} ${Math.abs(gap).toFixed(1)} 个百分点`);
  }
  if (profile.gravity != null) lines.push(`赛季引力 ${Number(profile.gravity).toFixed(2)}（防守压力，不是命中率）`);
  if (profile.leverage != null) lines.push(`赛季关键度 ${Number(profile.leverage).toFixed(2)}（官方原始值，不是这一球的胜率变化）`);
  return lines.map((line) => `<p>${line}</p>`).join("");
}

function setWatch(next) {
  if (MODES.indexOf(next) < 0) return;
  state.watch = next;
  const url = new URL(location.href);
  url.searchParams.set("mode", next);
  history.replaceState(null, "", url);
  syncWatch();
  renderSwitcher();
  const box = document.querySelector("#floor");
  if (box) delete box.dataset.signature;
  renderFloor();
  render();
}

function syncWatch() {
  document.querySelectorAll("[data-watch]").forEach((button) => {
    button.classList.toggle("on", button.dataset.watch === state.watch);
    button.setAttribute("aria-pressed", String(button.dataset.watch === state.watch));
  });
  const glossary = document.querySelector("#glossary");
  if (glossary) glossary.hidden = state.watch === "laofan";
}

function initialLang() {
  const query = params.get("lang");
  if (query === "en" || query === "zh") return query;
  try {
    const saved = localStorage.getItem("deepcourt-lang");
    if (saved === "en" || saved === "zh") return saved;
  } catch (error) {
    return "zh";
  }
  return "zh";
}

function setLang(next) {
  if (next !== "zh" && next !== "en") return;
  if (state.lang === next) return;
  const tracks = state.pack && state.pack.tracks;
  if (!tracks || !tracks[next]) return;
  state.lang = next;
  try { localStorage.setItem("deepcourt-lang", next); } catch (error) { /* 记不住就只留在这次打开里 */ }
  const url = new URL(location.href);
  url.searchParams.set("lang", next);
  history.replaceState(null, "", url);
  const playing = !els.reel.paused && !state.freeze;
  state.switchingTrack = { t: els.reel.currentTime || 0, playing };
  setReelSrc(tracks[next]);
  syncLang();
  renderSwitcher();
}

function syncLang() {
  const tracks = state.pack && state.pack.tracks;
  const nav = document.querySelector("#lang-switch");
  const line = document.querySelector("#track-script");
  const ready = Boolean(tracks && (tracks.zh || tracks.en));
  if (nav) nav.hidden = !ready;
  document.querySelectorAll("#lang-switch button").forEach((button) => {
    button.classList.toggle("on", button.dataset.lang === state.lang);
    button.disabled = !(tracks && tracks[button.dataset.lang]);
  });
  if (!line) return;
  const text = state.lang === "en" ? (state.pack && state.pack.scriptEn) : (state.pack && state.pack.scriptZh);
  line.hidden = !text;
  line.textContent = text ? `${state.lang === "en" ? "英文原声" : "中文解说"}：${text}` : "";
}

function renderFloor() {
  const box = document.querySelector("#floor");
  if (!box) return;
  const court = state.pack?.onCourt || {};
  const teams = Object.keys(court);
  if (!teams.length) {
    box.hidden = true;
    delete box.dataset.signature;
    return;
  }
  const home = state.pack.home || teams[0];
  const away = state.pack.away || teams.find((team) => team !== home) || "";
  const signature = [
    state.watch,
    home,
    away,
    (court[home] || []).map((player) => player.jersey).join(","),
    (court[away] || []).map((player) => player.jersey).join(","),
  ].join("|");
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature;
  box.hidden = false;
  box.classList.toggle("date", state.watch === "daikan");
  fillSide(document.querySelector("#floor-home"), "主队", home, court[home] || []);
  fillSide(document.querySelector("#floor-away"), "客队", away, court[away] || []);
}

function teamName(team) {
  return TEAM_MARK[team]?.name || team || "";
}

function fillSide(node, role, team, players) {
  if (!node) return;
  const color = TEAM_COLOR[team] || "#F3EDE2";
  const mark = TEAM_MARK[team] || {};
  const dating = state.watch === "daikan";
  const list = players.map((player) => {
    const en = player.full || player.name || "";
    const zh = player.name && player.name !== en ? player.name : "";
    const info = PLAYER_INFO[`${team}:${player.jersey}`] || {};
    const pos = info.position || player.position || "";
    const honors = info.stars == null ? "" : `<span class="honors">🌟 * ${info.stars}，🏆 * ${info.titles}</span>`;
    const face = dating ? faceHtml(team, player.jersey, en || zh) : "";
    return `<li>${face}<b>#${escapeHtml(player.jersey)}</b><span class="who"><strong>${escapeHtml(en)}</strong>${zh ? `<em>${escapeHtml(zh)}</em>` : ""}${honors}</span><i>${escapeHtml(pos)}</i></li>`;
  }).join("");
  const logo = mark.logo
    ? `<img class="team-logo" alt="" src="${mark.logo}" width="28" height="28" loading="lazy" decoding="async" onerror="this.remove()">`
    : "";
  const jersey = mark.jersey
    ? `<i class="jersey-swatch ${mark.swatch}" title="${escapeHtml(mark.jersey)}"></i>`
    : "";
  const jerseyText = mark.jersey ? `<em class="jersey-name">${escapeHtml(mark.jersey)}</em>` : "";
  node.innerHTML = `<header class="floor-head">${logo}<p style="color:${color}">${jersey}${role} · ${teamName(team)}${jerseyText}</p>${dating ? followHtml(team) : ""}</header><ul>${list}</ul>${dating ? companionHtml(team) : ""}`;
}

function followHtml(team) {
  const links = TEAM_LINKS[team];
  if (!links) return "";
  return `<span class="floor-actions"><a class="companion-add" href="${links.ics}">关注本队未来赛程</a></span>`;
}

function faceHtml(team, jersey, name) {
  const id = HEADSHOTS[`${team}:${jersey}`];
  if (!id) return `<span class="face-frame missing" aria-hidden="true"></span>`;
  const src = `headshots/${team.toLowerCase()}/${id}.png`;
  return `<span class="face-frame"><img alt="${escapeHtml(name)} 头部特写" src="${src}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.parentElement.classList.add('missing');this.remove()"></span>`;
}

function companionHtml(team) {
  const links = TEAM_LINKS[team];
  if (!links) return "";
  return `<div class="companion">
    <p class="companion-label">线上观看比赛订阅</p>
    <div class="companion-row">
      <a href="${WATCH_LINKS.league}" target="_blank" rel="noopener noreferrer">NBA League Pass</a>
      <a href="${WATCH_LINKS.tencent}" target="_blank" rel="noopener noreferrer">腾讯视频</a>
    </div>
    <div class="companion-row">
      <a href="${links.store}" target="_blank" rel="noopener noreferrer">浏览球队周边</a>
    </div>
  </div>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
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
  if (!els.ticks) return;
  els.ticks.innerHTML = state.events.map((item) => {
    const pos = Math.min(100, Math.max(0, (item.t / state.clipDuration) * 100));
    return `<i style="left:${pos}%"></i>`;
  }).join("");
}

function format(total) {
  const seconds = Math.max(0, Math.round(total));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function frameLabel() {
  const t = Number.isFinite(els.reel.currentTime) ? els.reel.currentTime : 0;
  const bug = scoreAt(t);
  return bug ? `${bug.period} ${bug.clock}` : format(t);
}

async function pollJob() {
  els.subhead.textContent = "任务读取中";
  let attempts = 0;
  while (attempts < 60) {
    attempts += 1;
    try {
      const job = await getJson(`/jobs/${encodeURIComponent(jobId)}`);
      els.subhead.textContent = `${statusText(job.status)} · ${job.gameMeta || job.filename || jobId}`;
      if (job.playbackUrl && state.currentSrc !== job.playbackUrl) setReelSrc(job.playbackUrl);
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

const SHARE_TARGETS = {
  kuaishou: { name: "快手", title: "发布作品", send: "发布", sent: "已发布", meta: "公开可见", accent: "#fe3666" },
  bilibili: { name: "哔哩哔哩", title: "投稿", send: "投稿", sent: "已投稿", meta: "公开", accent: "#fb7299" },
  wechat: { name: "视频号", title: "发表视频", send: "发表", sent: "已发表", meta: "公开", accent: "#07c160" },
  weibo: { name: "微博", title: "发微博", send: "发送", sent: "已发送", meta: "公开", accent: "#ff8200" },
  douyin: { name: "抖音", title: "发布作品", send: "发布", sent: "已发布", meta: "公开 · 所有人可见", accent: "#fe2c55" },
};

let shareTarget = SHARE_TARGETS.kuaishou;

function openShare(key) {
  const target = SHARE_TARGETS[key];
  const post = document.querySelector("#share-post");
  const cover = document.querySelector("#share-cover");
  const caption = document.querySelector("#share-caption");
  const status = document.querySelector("#share-status");
  const send = document.querySelector("#ks-send");
  const title = document.querySelector("#ks-title");
  const meta = document.querySelector("#share-meta");
  if (!target || !post || !cover || !caption || !status || !send || !title || !meta) return;
  shareTarget = target;
  title.textContent = target.title;
  meta.textContent = target.meta;
  send.disabled = false;
  send.textContent = target.send;
  send.style.color = target.accent;
  document.querySelectorAll("[data-share]").forEach((button) => {
    button.classList.toggle("on", button.dataset.share === key);
  });
  if (els.reel.readyState < 2 || !els.reel.videoWidth) {
    post.hidden = false;
    status.textContent = "画面还没出来。等这一帧加载完再点。";
    return;
  }
  pause(true);
  render();
  let dataUrl = "";
  try {
    dataUrl = captureFrame(els.reel);
  } catch (error) {
    post.hidden = false;
    status.textContent = "这一帧没能存成图片。";
    return;
  }
  const clock = frameLabel();
  downloadDataUrl(dataUrl, `deepcourt-${key}-${clock.replace(/:/g, "-")}.jpg`);
  cover.src = dataUrl;
  cover.alt = `${target.name} 当前帧 ${clock}`;
  caption.value = testCaption();
  status.textContent = "封面已保存到本机。文案是测试稿。";
  post.hidden = false;
}

function publishShareDraft() {
  const send = document.querySelector("#ks-send");
  const status = document.querySelector("#share-status");
  if (send) {
    send.disabled = true;
    send.textContent = shareTarget.sent;
    send.style.color = "";
  }
  if (status) status.textContent = `已在${shareTarget.name}用当前帧模拟发布。文案仍是测试稿。`;
}

function captureFrame(video) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.92);
}

function downloadDataUrl(url, name) {
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function testCaption() {
  const clock = frameLabel();
  const title = state.pack && state.pack.title ? state.pack.title : "当前回放";
  return `【测试文案】DEEP COURT\n${title}\n画面停在 ${clock}。封面用的是这一帧，正式文案下一步再生成。\n#NBA #DEEPCOURT`;
}

