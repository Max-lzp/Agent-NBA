/* The competition page with an explicitly archived, deterministic data panel. */
(function () {
  if (!window.DEEPCOURT_CONFIG?.offline) return;
  document.body.classList.add("offline");
  document.querySelector("#archive-note").hidden = false;
  const pack = window.DEEPCOURT_CLIPS?.flagg;
  const shot = pack?.shots?.[0];
  const root = document.querySelector("#deepcourt-analyst");
  if (!root || !shot) return;
  root.classList.add("archive-qa");
  const make = (tag, value, className) => {
    const node = document.createElement(tag); node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  root.append(make("h2", "读懂这一球"), make("p", "从原项目保存的数据中查看解释。以下为记录查询，不调用大模型，也不是实时问答。"));
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
  root.append(questions, answer, sources);
})();
