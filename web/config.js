window.DEEPCOURT_CONFIG = {
  apiBase: "/api",
  // 正式作品提交链接：CloudFront 根路径打开 game.html
  submitUrl: "https://d2vzd2batw1eqe.cloudfront.net/",
  githubUrl: "https://github.com/jasonlovegugu/nba-hackathon-team04/tree/feat/standard-player",
  // 回放页只展示考试片；考试片未发布时显示「考试片生成中」占位，不白屏
  examOnly: true,
  // 这些片段视为训练片/样片，examOnly 时在回放页隐藏（只隐藏，不删数据文件）
  // 中文解说待新版成片确认后再放回：删掉这一行即可
  hiddenTracks: ["zh"],
  sampleClips: ["flagg", "amen", "warm-probe", "rehearsal"],
};
