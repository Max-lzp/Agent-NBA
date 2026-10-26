"use strict";
const modes = {
  expert: ["深入看这一回合", "空间、出手选择与结果。", "7 号在侧翼三分命中。合成样例中，预期命中率为 35%；单次命中不能证明其长期投射能力。", "示例指标：出手价值 3 × 35% = 1.05 分；该指标不包含犯规、篮板等因素。"],
  new: ["先看懂发生了什么", "7 号投进一记三分。", "三分线外投进可得 3 分。这个回合让 A 队从领先 2 分变成领先 5 分。", "读图提示：橙色虚线代表出手路线，圆点表示球员位置。"],
  together: ["把这一球讲给身边的人", "他为什么站这么远投篮？", "因为三分线外投进能多拿 1 分，但通常也更难。可以一起关注：防守人靠近时，他还会选择出手吗？", "一起聊聊：你会选择靠近篮筐，还是尝试远投？本回合仅为合成教学示例。"],
};
document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-mode]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    ["mode-label", "headline", "explanation", "detail"].forEach((id, index) => {
      document.getElementById(id).textContent = modes[button.dataset.mode][index];
    });
  });
});
