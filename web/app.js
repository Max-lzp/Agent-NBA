const DEFAULT_HINT = "平均处理速度约为比赛时长 0.3× · 处理完成自动跳转回放页并生成分享卡片";

const fileTab = document.querySelector("#tab-file");
const urlTab = document.querySelector("#tab-url");
const drop = document.querySelector("#drop");
const fileInput = document.querySelector("#file");
const urlInput = document.querySelector("#url");
const go = document.querySelector("#go");
const hint = document.querySelector("#hint");
const host = document.querySelector("#host");

let picked = null;

if (location.hostname) host.textContent = location.host;

fileTab.addEventListener("click", () => selectTab("file"));
urlTab.addEventListener("click", () => selectTab("url"));
drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => takeFile(fileInput.files[0]));

["dragenter", "dragover"].forEach((name) => {
  drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.add("hot");
  });
});
drop.addEventListener("dragleave", () => drop.classList.remove("hot"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("hot");
  takeFile(event.dataTransfer.files[0]);
  selectTab("file");
});

go.addEventListener("click", () => start());

function selectTab(which) {
  const fileOn = which === "file";
  fileTab.setAttribute("aria-selected", String(fileOn));
  urlTab.setAttribute("aria-selected", String(!fileOn));
  drop.hidden = !fileOn;
}

function takeFile(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024 * 1024) {
    fail("单文件不能超过 2GB");
    return;
  }
  picked = file;
  document.querySelector("#drop-label").textContent = file.name;
  hint.classList.remove("error");
  hint.textContent = DEFAULT_HINT;
}

async function start() {
  const sourceUrl = urlInput.value.trim();
  const fileMode = fileTab.getAttribute("aria-selected") === "true" && picked;
  if (!fileMode && !sourceUrl) {
    fail("先选择视频，或粘贴 URL");
    return;
  }
  go.disabled = true;
  hint.classList.remove("error");
  hint.textContent = "正在创建分析任务…";
  try {
    const filename = fileMode ? picked.name : "";
    const created = await postJson("/jobs", {
      filename,
      contentType: fileMode ? picked.type || "application/octet-stream" : "",
      sourceUrl: fileMode ? "" : sourceUrl,
      gameMeta: gameMeta(fileMode ? "" : sourceUrl, filename),
    });
    if (fileMode && created.uploadUrl) {
      hint.textContent = "正在直传到 S3… 0%";
      await putFile(created.uploadUrl, created.uploadHeaders || {}, picked, (ratio) => {
        hint.textContent = `正在直传到 S3… ${Math.round(ratio * 100)}%`;
      });
    }
    location.href = `game.html?jobId=${encodeURIComponent(created.jobId)}`;
  } catch (error) {
    fail(error.message || "分析任务没有创建成功");
    go.disabled = false;
  }
}

function gameMeta(url, filename) {
  const match = url.match(/([a-z]{3}-vs-[a-z]{3}[^/?#]*)/i);
  if (match) return match[1].toLowerCase();
  if (filename) return filename.replace(/\.[^.]+$/, "").slice(0, 80);
  return "unknown";
}

function apiBase() {
  const fromQuery = new URLSearchParams(location.search).get("api");
  if (fromQuery) return fromQuery.replace(/\/$/, "");
  return (window.DEEPCOURT_CONFIG?.apiBase || "/api").replace(/\/$/, "");
}

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(`${apiBase()}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("连不上分析接口。部署 CloudFront 之后从站点打开，或在地址后加 ?api=API 地址");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `接口返回 ${response.status}`);
  return data;
}

function putFile(url, headers, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`S3 上传失败（${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error("S3 上传失败"));
    xhr.send(file);
  });
}

function fail(message) {
  hint.classList.add("error");
  hint.textContent = message;
}
