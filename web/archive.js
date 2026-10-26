/* The competition page with an explicitly archived, deterministic data panel. */
(function () {
  if (!window.DEEPCOURT_CONFIG?.offline) return;
  document.body.classList.add("offline");
  document.querySelector("#archive-note").hidden = false;
  const pack = window.DEEPCOURT_CLIPS?.flagg;
  const shot = pack?.shots?.[0];
  const video = document.querySelector("#reel");
  const insight = document.querySelector("#primary-insight");
  const root = document.querySelector("#deepcourt-analyst");
  if (!root || !shot) return;
  root.classList.add("archive-qa");
  const make = (tag, value, className) => {
    const node = document.createElement(tag); node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const probability = (shot.xfg * 100).toFixed(1);
  const readings = {
    xinfan: { label: "新球迷 · 先跟上这一回合", title: "一次近距离出手，怎么看懂？", rows: [
      ["发生了什么", `${shot.name}完成近距离出手，独行侠得到 ${shot.value} 分。先跟住 #${shot.number || "32"} 和球，再看球进入篮筐。`],
      ["为什么值得看", "进球只是结果。暂停在出手前，观察球员与篮筐、防守人的位置，才能慢慢看懂出手选择。"],
      ["数据说明什么", `这一投的预期命中率是 ${probability}%。可以理解为类似条件下的估计概率，不是这名球员必然能投进。`],
    ] },
    laofan: { label: "进阶数据 · 区分机会与结果", title: "命中结果，和机会质量分开看。", rows: [
      ["发生了什么", `${shot.name}，${shot.value} 分命中。原记录距离 ${shot.distance} 英尺，区域为${shot.zone || "近筐区域"}。`],
      ["为什么值得看", "将出手条件、最终结果与防守记录分开观察。传接球和领防标签来自视觉快照，不据此宣称已自动识别整套战术。"],
      ["数据说明什么", `xFG ${probability}%，简化预期得分约 ${(shot.value * shot.xfg).toFixed(2)} 分；不含犯规和篮板。一次命中不能证明球员长期水平。`],
    ] },
    daikan: { label: "一起看 · 把这一球讲给身边的人", title: "先找球员，再一起猜下一步。", rows: [
      ["发生了什么", `一起找一找 #${shot.number || "32"}：${shot.name}。他把球送进篮筐，这次进球值 ${shot.value} 分。`],
      ["为什么值得看", "可以问身边的人：靠近篮筐是不是就一定能进？先暂停看防守人站在哪里，再一起看结果。"],
      ["数据说明什么", `${probability}% 不是考试分数，而是这次出手的估计命中概率。它帮我们理解机会，不替我们决定喜欢哪位球员。`],
    ] },
  };
  function describe(mode) {
    if (!insight) return;
    const reading = readings[mode] || readings.xinfan;
    insight.replaceChildren(make("p", reading.label, "insight-kicker"), make("h2", reading.title));
    reading.rows.forEach(([title, content], index) => {
      const row = make("article", "", "insight-row");
      row.dataset.explain = String(index);
      row.append(make("h3", `${String(index + 1).padStart(2, "0")} / ${title}`), make("p", content)); insight.append(row);
    });
    const source = make("details", "", "insight-source"); source.append(make("summary", "这些解释从哪里来？"), make("p", "回合和出手统计来自原项目保存的比赛记录；领防和传接球为视觉处理快照。这里展示编辑整理的解释，不调用实时大模型。")); insight.append(source);
  }
  const currentMode = document.querySelector('[data-watch][aria-pressed="true"]')?.dataset.watch;
  describe(currentMode);
  window.addEventListener("deepcourt:mode", (event) => describe(event.detail.mode));
  const chapters = document.querySelector("#replay-chapters");
  const freezeEvent = pack.events.find((event) => event.source === "rendered_replay" && event.shot);
  for (const [label, subtitle, time] of [["01 回合开始", "跟住球和球员", 0], ["02 看出手", "暂停观察位置", shot.release?.t || shot.t], ["03 定格复盘", "对照这次出手", freezeEvent?.t || shot.t]]) {
    const button = make("button", "", "chapter"); button.type = "button"; button.disabled = video.readyState < 1; button.dataset.time = String(time);
    button.append(make("b", label), make("span", subtitle));
    video.addEventListener("loadedmetadata", () => { button.disabled = false; });
    video.addEventListener("error", () => { button.disabled = true; });
    button.addEventListener("click", () => { if (video.readyState < 1) return; video.pause(); video.currentTime = time; }); chapters?.append(button);
  }
  const annotateChapter = () => {
    let index = 0; const buttons = [...(chapters?.querySelectorAll("button") || [])];
    buttons.forEach((button, i) => { if (video.currentTime >= Number(button.dataset.time) - .05) index = i; });
    buttons.forEach((button, i) => button.setAttribute("aria-current", String(i === index)));
  };
  video.addEventListener("timeupdate", annotateChapter); annotateChapter();
  root.append(make("h2", "再问一步"), make("p", "从这次回合的已保存记录中找答案。"));
  const questions = make("div", "", "archive-questions");
  const answer = make("p", "选择一个问题，查看对应的历史记录。", "archive-answer");
  answer.setAttribute("aria-live", "polite");
  const sources = make("p", "", "archive-source");
  const rows = [
    ["这一投有多难？", () => `${shot.name}这记${shot.value}分出手，保存的预期命中率为 ${(shot.xfg * 100).toFixed(1)}%。${shot.distance ? `距离 ${shot.distance} 英尺。` : ""}这是对应出手的数据，不是当前实时模型判断。`, "原项目 replay/clips.js · 出手统计快照"],
    ["谁在防守他？", () => {
      const defender = shot.defender;
      return defender?.name ? `历史视觉识别记录：${defender.display || defender.name} #${defender.number}，出手前贴防 ${Number(defender.guard_seconds).toFixed(1)} 秒。画面识别可能存在误差，不将其视为官方确认。` : "原记录未可靠识别出领防人，不补充猜测。";
    }, "原项目 replay/clips.js · 视觉识别记录"],
    ["为什么回放会定格？", () => "这份比赛成片已经烧录了出手瞬间、投篮人和领防人的标记、赛季数据与分区热区图。网页不会再触发一次额外定格。可点击时间线的“出手定格回看”查看。", "原项目 pipeline/present/freeze.py · 成片流程"],
  ];
  for (const [question, response, source] of rows) {
    const button = make("button", question); button.type = "button";
    button.addEventListener("click", () => { answer.textContent = response(); sources.textContent = `来源：${source}`; });
    questions.append(button);
  }
  const answerArea = make("details", "", "guided-followup");
  answerArea.append(make("summary", "选择一个问题"), questions, answer, sources);
  root.append(answerArea);
})();
