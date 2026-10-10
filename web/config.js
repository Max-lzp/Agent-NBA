window.DEEPCOURT_CONFIG = {
  apiBase: "/api",
  // 正式作品提交链接：CloudFront 根路径打开 game.html
  submitUrl: "https://d2vzd2batw1eqe.cloudfront.net/",
  githubUrl: "https://github.com/jasonlovegugu/nba-hackathon-team04/tree/feat/standard-player",
  // 回放页只展示考试片；考试片未发布时显示「考试片生成中」占位，不白屏
  examOnly: true,
  // 这些片段视为训练片/样片，examOnly 时在回放页隐藏（只隐藏，不删数据文件）
  // 中文解说待新版成片确认后再放回：删掉这一行即可
  // 名签只用成片里烧进去的那一层，网页不再叠画
  webNames: false,
  hiddenTracks: ["zh"],
  // 问分析师的示例问题；数字来自官方 shot quality、逐回合、对位和赛季数据
  analystPresets: [
    {
      q: "克里斯蒂这记 3+1 有多难？",
      zh: "这是一记 24.85 英尺的后撤步打板三分，出手时进攻时间只剩不到 1 秒，预期命中率只有 30.0%，明显低于独行侠全场平均的 51.7%。他投进了，还造成小史密斯犯规，加罚也命中，一个回合拿下 4 分。",
      en: "A 24.85-ft step-back bank three with under a second on the shot clock: expected make just 30.0%, well below Dallas' 51.7% game average. He hit it, drew the foul on Smith Jr. and made the free throw for a 4-point play.",
      sources: ["shotquality: shot 273", "playbyplay 0022500356"]
    },
    {
      q: "小史密斯这次封盖有多难得？",
      zh: "被盖的是克里斯蒂 8.95 英尺的突破抛投，这一投预期命中率 17.7%。小史密斯本赛季 71 次盖帽、场均 0.9 次；他防守时，10 英尺内对手命中率 53.6%（226/422），比这些球员平时的 58.9% 低 5.3 个百分点。",
      en: "He blocked Christie's 8.95-ft driving floater (expected make 17.7%). Smith Jr. has 71 blocks this season (0.9 per game), and opponents shoot 53.6% within 10 ft against him (226/422), 5.3 points below their usual 58.9%.",
      sources: ["shotquality: shot 270", "leaguedashplayerstats HOU", "defense: within 10 ft"]
    },
    {
      q: "克里斯蒂这赛季三分准不准？",
      zh: "本赛季他三分命中率 40.4%（180/446），弧顶以上三分 37.4%（105/281）。这场被小史密斯防了 59 秒，2 投 1 中，三分 1 投 1 中，被盖 1 次，造成投篮犯规 1 次，一共拿下 4 分。",
      en: "This season he shoots 40.4% from three (180/446) and 37.4% above the break (105/281). Guarded by Smith Jr. for 0:59 tonight: 1-of-2, 1-of-1 from three, blocked once, one shooting foul drawn, 4 points.",
      sources: ["player_shooting_distance_aligned DAL", "boxscorematchupsv3 0022500356"]
    }
  ],
  sampleClips: ["flagg", "amen", "warm-probe", "rehearsal"],
};
