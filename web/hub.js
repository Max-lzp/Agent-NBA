(function () {
  const cfg = window.DEEPCOURT_CONFIG || {};
  const submit = document.getElementById("submit-link");
  const label = document.getElementById("submit-label");
  const hint = document.getElementById("submit-hint");
  const originEl = document.getElementById("hub-origin");
  const github = document.getElementById("github-link");

  const origin = window.location.origin;
  if (originEl) originEl.textContent = `当前打开：${origin}/hub.html`;

  const submitUrl = (cfg.submitUrl || "").trim() || `${origin}/`;
  const configured = Boolean((cfg.submitUrl || "").trim());

  if (submit) {
    submit.href = submitUrl;
    if (configured) {
      label.textContent = submitUrl.replace(/\/$/, "") || submitUrl;
      hint.textContent = "已配置 · 复制这一行交给组委会";
      submit.classList.add("ready");
    } else if (/cloudfront\.net|amazonaws\.com/i.test(origin)) {
      label.textContent = origin + "/";
      hint.textContent = "当前域名即正式入口 · 建议写回 config.js 的 submitUrl";
      submit.classList.add("ready");
    } else {
      label.textContent = "部署后填入 CloudFront 根链接";
      hint.textContent = "本地预览中 · 正式提交请用发布后的根路径";
    }
  }

  if (github && cfg.githubUrl) github.href = cfg.githubUrl;
})();
