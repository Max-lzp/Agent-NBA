/* Deep Court 问分析师。
 * 回放页接入时，在 config.js 之后加上：
 *   <section id="deepcourt-analyst"></section>
 *   <script src="analyst.js"></script>
 * 放在 web/game.html 的 </main> 之前即可。jobId 跟回放页一样读 ?jobId=。
 * 也可以给容器写 data-job-id，或者设置 window.DEEPCOURT_JOB_ID。
 */
(function () {
  // Offline replay uses archive.js to show archived records, never cloud calls.
  if (window.DEEPCOURT_CONFIG?.offline) return;
  var STYLE = [
    "#deepcourt-analyst{margin:28px 0 8px;padding:18px 18px 16px;border:1px solid var(--line,#332c24);border-radius:16px;background:var(--panel,#1c1814);color:var(--cream,#f3ede2)}",
    "#deepcourt-analyst *{box-sizing:border-box}",
    "#deepcourt-analyst h2{margin:0 0 6px;font-size:20px}",
    "#deepcourt-analyst .dc-kicker{margin:0 0 12px;color:var(--muted,#a3988a);font-size:13px}",
    "#deepcourt-analyst .dc-highlights{display:grid;gap:8px;margin:0 0 14px;padding:0;list-style:none}",
    "#deepcourt-analyst .dc-highlights li{padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.03)}",
    "#deepcourt-analyst .dc-en{display:block;margin-top:4px;color:var(--muted,#a3988a);font-size:14px}",
    "#deepcourt-analyst form{display:flex;gap:8px;flex-wrap:wrap}",
    "#deepcourt-analyst input{flex:1 1 220px;min-width:0;padding:10px 12px;border-radius:999px;border:1px solid var(--line,#332c24);background:#14110e;color:inherit}",
    "#deepcourt-analyst button{padding:10px 16px;border:0;border-radius:999px;background:var(--orange,#f1681e);color:#14110e;font-weight:700}",
    "#deepcourt-analyst button[disabled]{opacity:.6}",
    "#deepcourt-analyst .dc-answer{margin-top:12px}",
    "#deepcourt-analyst .dc-sources{margin:8px 0 0;padding:0;list-style:none;color:var(--teal,#5fbfa9);font-size:12px;word-break:break-all}",
    "#deepcourt-analyst .dc-presets{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}",
    "#deepcourt-analyst .dc-presets button{background:rgba(241,104,30,.12);color:var(--cream,#f3ede2);border:1px solid rgba(241,104,30,.45);font-weight:500;font-size:14px;cursor:pointer}",
    "#deepcourt-analyst .dc-presets button:hover{background:rgba(241,104,30,.25)}",
    "#deepcourt-analyst .dc-status{min-height:1.2em;margin:8px 0 0;color:var(--muted,#a3988a);font-size:13px}",
  ].join("");

  function jobIdFrom(root) {
    var fromRoot = root.getAttribute("data-job-id");
    if (fromRoot) return fromRoot;
    if (window.DEEPCOURT_JOB_ID) return String(window.DEEPCOURT_JOB_ID);
    return new URLSearchParams(window.location.search).get("jobId") || "";
  }

  function apiBase() {
    var config = window.DEEPCOURT_CONFIG || {};
    var base = config.apiBase || "/api";
    return base.replace(/\/$/, "");
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function sourcesOf(item) {
    var list = el("ul", "dc-sources");
    (item && item.sources || []).forEach(function (source) {
      list.appendChild(el("li", "", source));
    });
    return list;
  }

  function bilingual(parent, zh, en) {
    parent.appendChild(el("span", "", zh || ""));
    if (en) parent.appendChild(el("span", "dc-en", en));
  }

  function mount(root) {
    if (!root || root.getAttribute("data-ready") === "1") return;
    root.setAttribute("data-ready", "1");
    if (!document.getElementById("deepcourt-analyst-style")) {
      var style = document.createElement("style");
      style.id = "deepcourt-analyst-style";
      style.textContent = STYLE;
      document.head.appendChild(style);
    }
    var jobId = jobIdFrom(root);
    root.appendChild(el("p", "dc-kicker", "TEAM 04 · 只引用查到的数据"));
    root.appendChild(el("h2", "", "问分析师"));
    var highlights = el("ol", "dc-highlights");
    root.appendChild(highlights);
    // 示例问题：答案写在 config.js 的 analystPresets 里，每条都带数据来源
    var presets = ((window.DEEPCOURT_CONFIG || {}).analystPresets || []).filter(function (p) { return p && p.q && p.zh; });
    var presetBox = el("div", "dc-presets");
    presets.forEach(function (preset) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.textContent = preset.q;
      chip.addEventListener("click", function () {
        input.value = preset.q;
        setStatus("");
        renderAnswer({ answer: { zh: preset.zh, en: preset.en || "", sources: preset.sources || [] } });
      });
      presetBox.appendChild(chip);
    });
    if (presets.length) root.appendChild(presetBox);
    var form = document.createElement("form");
    var input = document.createElement("input");
    input.type = "text";
    input.name = "question";
    input.placeholder = "问问这段回放，比如这一球的预期命中率";
    input.setAttribute("aria-label", "问分析师");
    input.maxLength = 400;
    var button = document.createElement("button");
    button.type = "submit";
    button.textContent = "提问";
    form.appendChild(input);
    form.appendChild(button);
    root.appendChild(form);
    var status = el("p", "dc-status", "");
    status.setAttribute("role", "status");
    root.appendChild(status);
    var answer = el("div", "dc-answer");
    answer.setAttribute("aria-live", "polite");
    root.appendChild(answer);

    function setStatus(text) {
      status.textContent = text || "";
    }

    function renderHighlights(analysis) {
      highlights.textContent = "";
      var seen = {};
      var rows = ((analysis && analysis.highlights) || []).filter(function (item) {
        var key = (item && (item.zh || item.en) || "").trim();
        if (!key || seen[key] || /没有更多|No further highlight/i.test(key)) return false;
        seen[key] = 1;
        return true;
      });
      if (!rows.length && presets.length) {
        highlights.hidden = true;
        return;
      }
      highlights.hidden = false;
      if (!rows.length) {
        highlights.appendChild(el("li", "", jobId ? "这场还没有三条亮点。" : "打开带 jobId 的回放后，这里会显示三条亮点。"));
        return;
      }
      rows.slice(0, 3).forEach(function (item) {
        var li = document.createElement("li");
        bilingual(li, item.zh, item.en);
        li.appendChild(sourcesOf(item));
        highlights.appendChild(li);
      });
    }

    function renderAnswer(payload) {
      answer.textContent = "";
      var body = payload.answer || {};
      if (payload.error && !body.zh && !body.en) {
        answer.appendChild(el("p", "", payload.error));
        return;
      }
      var block = el("div", "");
      bilingual(block, body.zh || payload.error || "", body.en || "");
      block.appendChild(sourcesOf(body));
      answer.appendChild(block);
    }

    function loadHighlights() {
      if (!jobId) {
        renderHighlights(null);
        return;
      }
      fetch(apiBase() + "/agent/analysis/" + encodeURIComponent(jobId))
        .then(function (response) {
          if (response.status === 404) return null;
          if (!response.ok) throw new Error("亮点暂时读不到");
          return response.json();
        })
        .then(function (payload) {
          renderHighlights(payload && (payload.analysis || payload));
        })
        .catch(function () {
          renderHighlights(null);
          setStatus("亮点暂时读不到。");
        });
    }

    function ask(question, attempt) {
      setStatus(attempt ? "分析师还在醒，再试一次…" : "分析师在查数据…");
      button.disabled = true;
      return fetch(apiBase() + "/agent/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ jobId: jobId, question: question }),
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if ((response.status === 503 || response.status === 504 || response.status === 502) && !attempt) {
            return ask(question, 1);
          }
          renderAnswer(payload);
          setStatus(response.ok ? "" : (payload.error || "这次没有答上来，可以再问一次。"));
        });
      }).catch(function () {
        if (!attempt) return ask(question, 1);
        setStatus("网络没有连上，可以再问一次。");
      }).then(function () {
        button.disabled = false;
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var question = input.value.trim();
      if (!jobId) {
        setStatus("还没有 jobId。回放地址需要带上 ?jobId=。");
        return;
      }
      if (!question) {
        setStatus("先写一个问题。");
        return;
      }
      ask(question, 0);
    });

    loadHighlights();
  }

  function boot() {
    var nodes = document.querySelectorAll("#deepcourt-analyst,[data-deepcourt-analyst]");
    Array.prototype.forEach.call(nodes, mount);
  }

  window.DeepCourtAnalyst = { mount: mount };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
