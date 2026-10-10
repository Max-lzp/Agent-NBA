(function () {
  const OPS = "http://127.0.0.1:8791";
  const banner = document.getElementById("hub-banner");
  const ghFacts = document.getElementById("gh-facts");
  const ghFiles = document.getElementById("gh-files");
  const cfFacts = document.getElementById("cf-facts");
  const logEl = document.getElementById("hub-log");
  const logTitle = document.getElementById("log-title");
  const pushBtn = document.getElementById("gh-push");
  const deployBtn = document.getElementById("cf-deploy");
  const refreshBtn = document.getElementById("gh-refresh");
  const ghOpen = document.getElementById("gh-open");
  const cfOpen = document.getElementById("cf-open");

  let polling = null;
  let busy = false;

  function facts(rows) {
    return rows.map(function (row) {
      return "<div><dt>" + row[0] + "</dt><dd>" + row[1] + "</dd></div>";
    }).join("");
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function syncLine(git) {
    if (!git.remoteHasBranch) return "远端还没有这个分支";
    const ahead = git.ahead || 0;
    const behind = git.behind || 0;
    if (ahead === 0 && behind === 0) return "已和远端对齐";
    const parts = [];
    if (ahead) parts.push("本地超前 " + ahead + " 个提交");
    if (behind) parts.push("落后远端 " + behind + " 个提交");
    return parts.join("，");
  }

  function render(data) {
    const git = data.github || {};
    const cf = data.cloudfront || {};
    ghFacts.innerHTML = facts([
      ["仓库", esc(git.repo || "")],
      ["分支", esc(git.branch || "")],
      ["与远端", esc(syncLine(git))],
      ["最近提交", esc((git.head || "") + " " + (git.subject || "")).trim()],
      ["未提交", esc((git.modified || 0) + " 个修改，" + (git.untracked || 0) + " 个未跟踪")],
    ]);
    const sample = git.sample || [];
    if (sample.length) {
      ghFiles.hidden = false;
      const more = (git.modified || 0) + (git.untracked || 0) - sample.length;
      ghFiles.innerHTML = sample.map(function (path) {
        return "<li>" + esc(path) + "</li>";
      }).join("") + (more > 0 ? "<li>…还有 " + more + " 个</li>" : "");
    } else {
      ghFiles.hidden = true;
      ghFiles.innerHTML = "";
    }
    if (ghOpen && git.repo) ghOpen.href = git.repo;

    cfFacts.innerHTML = facts([
      ["链接", esc(cf.url || "")],
      ["分配", esc((cf.distributionId || "—") + (cf.distributionStatus ? " · " + cf.distributionStatus : ""))],
      ["栈", esc((cf.stack || "") + (cf.stackStatus ? " · " + cf.stackStatus : ""))],
      ["账号", esc((cf.profile || "nba") + (cf.account ? " · " + cf.account : ""))],
      ["读状态", esc(cf.error || "正常")],
    ]);
    if (cfOpen && cf.url) cfOpen.href = cf.url;
    renderJob(data.job || {});
  }

  function renderJob(job) {
    const state = job.state || "idle";
    busy = state === "running";
    pushBtn.disabled = busy;
    deployBtn.disabled = busy;
    const title = {
      idle: "任务输出",
      running: (job.kind === "cloudfront" ? "正在部署 CloudFront" : "正在推送到 GitHub"),
      ok: "完成",
      failed: "失败",
    }[state] || "任务输出";
    logTitle.textContent = title;
    logEl.textContent = job.log || (state === "idle" ? "还没有任务。" : "等待输出…");
    if (state === "running" && !polling) {
      polling = setInterval(pullJob, 1500);
    }
    if (state !== "running" && polling) {
      clearInterval(polling);
      polling = null;
    }
  }

  function offline(message) {
    banner.hidden = false;
    banner.textContent = message;
    pushBtn.disabled = true;
    deployBtn.disabled = true;
  }

  async function load() {
    banner.hidden = true;
    try {
      const res = await fetch(OPS + "/status");
      if (!res.ok) throw new Error("status " + res.status);
      render(await res.json());
    } catch (err) {
      offline("本地控制服务没开。在 team-repo 里运行 python3 scripts/ops_server.py，然后刷新。");
      ghFacts.innerHTML = facts([["状态", "连不上 127.0.0.1:8791"]]);
      cfFacts.innerHTML = facts([["链接", "https://d2vzd2batw1eqe.cloudfront.net/"]]);
    }
  }

  async function pullJob() {
    try {
      const res = await fetch(OPS + "/job");
      const job = await res.json();
      renderJob(job);
      if (job.state !== "running") load();
    } catch (err) {
      offline("控制服务中断。");
    }
  }

  async function post(path, confirmText) {
    if (busy) return;
    if (!window.confirm(confirmText)) return;
    banner.hidden = true;
    try {
      const res = await fetch(OPS + path, { method: "POST" });
      const body = await res.json();
      if (!res.ok || body.ok === false) {
        offline(body.error || "没有启动。");
        return;
      }
      pullJob();
    } catch (err) {
      offline("本地控制服务没开。在 team-repo 里运行 python3 scripts/ops_server.py，然后刷新。");
    }
  }

  refreshBtn.addEventListener("click", load);
  pushBtn.addEventListener("click", function () {
    post("/github/push", "把当前分支已经提交的内容推到 GitHub？未提交的文件不会带上。");
  });
  deployBtn.addEventListener("click", function () {
    post("/cloudfront/deploy", "把当前网页部署到 CloudFront？使用 profile nba，enableP1 和 enableP2 都关掉。");
  });

  load();
})();
