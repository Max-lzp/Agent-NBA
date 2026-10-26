import { SAMPLE_ID, sampleEvents, timeLabel, validateTimeline } from "./timeline.js";

const $ = (id) => document.getElementById(id);
const video = $("video");
let events = [], source = null, mode = "new", active = -1, objectURL = null, loadSerial = 0;
const status = (message) => { $("status").textContent = message; };
const note = (event) => event.notes[mode];
function availability(ready) {
  for (const id of ["export", "timeline-file", "add-event"]) $(id).disabled = !ready;
}
function details() {
  const event = events[active];
  $("event-time").textContent = event ? timeLabel(event.t) : timeLabel(video.currentTime || 0);
  $("event-heading").textContent = event?.label || "还没有事件标记";
  $("event-description").textContent = event ? note(event) : "播放视频，在值得回看的时刻暂停，再添加你的观察。";
  $("event-origin").textContent = event?.origin || "没有运行自动识别或大模型分析。";
  $("remove-event").disabled = !event || !source;
  $("timeline").querySelectorAll("button").forEach((button, index) => button.setAttribute("aria-current", String(index === active)));
}
function timeline() {
  $("timeline").replaceChildren();
  events.forEach((event, index) => {
    const li = document.createElement("li"), button = document.createElement("button"), time = document.createElement("time"), label = document.createElement("span");
    button.type = "button"; time.textContent = timeLabel(event.t); label.textContent = event.label;
    button.append(time, label); button.addEventListener("click", () => {
      if (!source) return;
      video.pause(); video.currentTime = event.t; active = index; details();
    });
    li.append(button); $("timeline").append(li);
  });
  details();
}
function followTime() {
  // Before the first timestamp there is no evidence to display yet.
  let index = -1;
  events.forEach((event, i) => { if (event.t <= video.currentTime + 0.01) index = i; });
  if (active !== index) { active = index; details(); }
}
async function load(kind, file) {
  const serial = ++loadSerial;
  video.pause(); availability(false); source = null; active = -1;
  video.onloadedmetadata = null; video.onerror = null;
  video.removeAttribute("src"); video.load();
  events = kind === "sample" ? structuredClone(sampleEvents) : [];
  $("lookup-result").textContent = "";
  if (objectURL) { URL.revokeObjectURL(objectURL); objectURL = null; }
  const descriptor = kind === "sample" ? { kind, id: SAMPLE_ID } : { kind, name: file.name, size: file.size };
  $("source").textContent = kind === "sample" ? "自制教学动画 · 合成回合 · 无真实比赛画面" : `本地文件：${file.name} · 未上传`;
  status("正在读取视频…");
  video.onloadedmetadata = () => {
    if (!Number.isFinite(video.duration) || video.duration <= 0) { status("视频没有可用时长，请选择其他文件"); return; }
    source = { ...descriptor, duration: video.duration };
    availability(true); active = events.length ? 0 : -1; timeline();
    status(kind === "sample" ? "教学样例已就绪。点击播放或选择时间线事件。" : "本地视频已就绪。可以播放并添加标记；未执行自动识别。");
  };
  video.onerror = () => { source = null; availability(false); $("remove-event").disabled = true; status("视频无法解码或文件缺失。请尝试 H.264 编码的 MP4 文件。"); };
  timeline();
  try {
    let blob = file;
    if (kind === "sample") {
      // The small bundled sample is fully buffered so seeking also works on
      // static preview servers without HTTP Range support.
      const response = await fetch("media/teaching.mp4");
      if (!response.ok) throw new Error("教学视频暂时无法读取");
      blob = await response.blob();
    }
    if (serial !== loadSerial) return;
    video.src = objectURL = URL.createObjectURL(blob);
    video.load();
  } catch (error) {
    if (serial !== loadSerial) return;
    status(`${error.message}。可选择本地视频继续使用。`);
  }
}
video.addEventListener("timeupdate", followTime);
$("sample").addEventListener("click", () => load("sample"));
$("video-file").addEventListener("change", (event) => {
  const file = event.target.files[0]; if (!file) return;
  if (!file.size) { status("文件为空，请选择有效视频"); return; }
  load("local", file); event.target.value = "";
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  mode = button.dataset.mode;
  $("lookup-result").textContent = "";
  document.querySelectorAll("[data-mode]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  details();
}));
$("annotation-form").addEventListener("submit", (event) => {
  event.preventDefault(); if (!source) return;
  const label = $("event-title").value.trim(), message = $("event-note").value.trim();
  if (!label || !message) { status("请填写事件名称和观察说明"); return; }
  if (events.length >= 1000) { status("最多支持 1000 个事件，请先导出并整理"); return; }
  const entry = { t: Math.min(video.currentTime, Math.max(0, video.duration - 0.01)), label, origin: "用户手动标记（未独立核验）", notes: { expert: message, new: message, together: message } };
  events.push(entry); events.sort((a, b) => a.t - b.t); active = events.indexOf(entry);
  video.pause(); timeline(); $("annotation-form").reset(); $("lookup-result").textContent = ""; status("标记已添加。请在刷新或切换视频前导出时间线。");
});
$("remove-event").addEventListener("click", () => {
  if (!source || active < 0) return;
  events.splice(active, 1); active = -1; followTime(); timeline(); $("lookup-result").textContent = ""; status("标记已删除，请导出更新后的时间线保存。");
});
$("export").addEventListener("click", () => {
  if (!source) return;
  const blob = new Blob([JSON.stringify({ schemaVersion: 1, source, events }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = "deepcourt-timeline.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  status("已导出时间线 JSON，其中不包含视频。请同时保留原视频文件。");
});
$("timeline-file").addEventListener("change", async (event) => {
  const file = event.target.files[0], currentSource = source;
  event.target.value = "";
  if (!file || !currentSource) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error("JSON 文件不能超过 5 MB");
    const data = JSON.parse(await file.text());
    if (source !== currentSource) return;
    const imported = validateTimeline(data, currentSource);
    events = imported; active = -1; followTime(); timeline(); $("lookup-result").textContent = ""; status(`已导入 ${events.length} 个事件，将其作为用户记录展示；不是 AI 识别结果。`);
  } catch (error) { status(`导入失败：${error.message}。现有标记已保留。`); }
});
$("lookup-form").addEventListener("submit", (event) => {
  event.preventDefault(); const query = $("query").value.trim().toLocaleLowerCase();
  if (!query) return;
  const found = events.filter((entry) => `${entry.label} ${note(entry)}`.toLocaleLowerCase().includes(query));
  $("lookup-result").textContent = found.length ? found.slice(0, 10).map((entry) => `${timeLabel(entry.t)} · ${entry.label}\n${note(entry)}\n来源：${entry.origin}`).join("\n\n") : "当前记录中没有匹配内容。可以换一个事件关键词；不会推测未记录的事实。";
});
window.addEventListener("beforeunload", () => { if (objectURL) URL.revokeObjectURL(objectURL); });
load("sample");
