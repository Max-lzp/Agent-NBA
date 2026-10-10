// 进球后的出手定格和右侧分区热区图。game.js 负责播放状态，这里只出画面和文字。
(function () {
  const FREEZE_MS = 5000;
  const PX = 4.4;
  const BASKET_Y = 5.25;
  const CELL = 1;
  const RED = "#FF2B2B";
  const TEAM_ZH = { HOU: "火箭", DAL: "独行侠" };
  const ZONE_ZH = {
    restricted: "合理冲撞区", paint: "油漆区", midrange: "中距离",
    corner3_l: "左底角三分", corner3_r: "右底角三分", wing3_l: "左侧翼三分", wing3_r: "右侧翼三分", top3: "弧顶三分",
  };
  const ZONE_LABEL_AT = {
    restricted: [0, 1.2], paint: [0, 10], midrange: [-14, 11], corner3_l: [-23.6, 3], corner3_r: [23.6, 3],
    wing3_l: [-19, 22], wing3_r: [19, 22], top3: [0, 26.5],
  };

  const pct = (v, digits = 1) => `${(Number(v) * 100).toFixed(digits)}%`;

  function zoneOf(x, y) {
    const d = Math.hypot(x, y);
    const corner = Math.sqrt(23.75 ** 2 - 22 ** 2);
    const three = y <= corner ? Math.abs(x) >= 22 : d >= 23.75;
    if (three) {
      if (Math.abs(x) < 8) return "top3";
      const side = x < 0 ? "l" : "r";
      return y <= 9 ? `corner3_${side}` : `wing3_${side}`;
    }
    if (d <= 4) return "restricted";
    if (Math.abs(x) <= 8 && y <= 14) return "paint";
    return "midrange";
  }

  function goalTime(pack, shot) {
    const release = shot.release?.t;
    const rows = pack.scorebug || [];
    const before = [...rows].reverse().find((row) => row.t <= release + 0.05);
    const after = rows.find((row) => row.t > release && before
      && row.awayScore + row.homeScore > before.awayScore + before.homeScore);
    const limit = (pack.duration || 0) - 0.4;
    const at = after ? after.t + 0.3 : release + 1.5;
    return limit > release ? Math.min(at, limit) : at;
  }

  function candidates(pack) {
    return (pack.shots || []).filter((shot) => shot.made && shot.release && Number.isFinite(shot.release.t));
  }

  function scoreLine(pack, shot) {
    const rows = pack.scorebug || [];
    const bug = [...rows].reverse().find((row) => row.t <= shot.release.t + 0.05) || rows[0];
    if (!bug) return "";
    const mine = shot.team === bug.away ? bug.awayScore - bug.homeScore : bug.homeScore - bug.awayScore;
    const gap = mine === 0 ? "打平" : `${TEAM_ZH[shot.team] || shot.team}${mine > 0 ? "领先" : "落后"} ${Math.abs(mine)} 分`;
    return `${bug.period} ${bug.clock} · ${bug.awayName} ${bug.awayScore} : ${bug.homeScore} ${bug.homeName} · ${gap}`;
  }

  function offenseLines(shot) {
    const lines = [];
    const s = shot.season;
    if (s) lines.push(`赛季场均 ${s.pts} 分 · 投篮 ${pct(s.fg_pct)} · 三分 ${pct(s.fg3_pct)}`);
    if (shot.xfg != null) lines.push(`这一投预期命中率 ${pct(shot.xfg)} · ${shot.zone || ""} ${shot.distance ? Number(shot.distance).toFixed(0) + " 英尺" : ""}`.trim());
    const m = shot.season_margin;
    if (m && m.ts != null) {
      let line = `${m.bucket_zh}真实命中率 ${pct(m.ts)}`;
      if (m.team_ts != null) {
        const diff = (Number(m.ts) - Number(m.team_ts)) * 100;
        line += `，比全队同档${diff >= 0 ? "高" : "低"} ${Math.abs(diff).toFixed(1)} 个百分点`;
      }
      lines.push(line);
    }
    return lines;
  }

  function defenseLines(shot) {
    const lines = [];
    const near = shot.nearest;
    const d = shot.defender;
    if (near) {
      const who = near.display || `${TEAM_ZH[near.team] || near.team || ""}球员${near.number ? " #" + near.number : "，号码没读出"}`;
      lines.push(`最近防守人：${who}（画面）`);
    }
    if (d) {
      const head = d.source === "matchup" ? "本场主防（对位表）" : "防守人";
      const vs = d.vs_shooter;
      let line = `${head}：${d.display || d.name} #${d.number}`;
      if (vs) line += ` · 对位 ${Math.round(vs.seconds / 60)} 分钟，${shot.name} ${vs.fgm}/${vs.fga}`;
      lines.push(line);
      if (d.season) lines.push(`赛季场均 抢断 ${d.season.stl} · 盖帽 ${d.season.blk} · 防守篮板 ${d.season.dreb}`);
    }
    return lines;
  }

  function cardHtml(pack, shot, left) {
    const offense = offenseLines(shot).map((line) => `<p>${line}</p>`).join("");
    const defense = defenseLines(shot).map((line) => `<p>${line}</p>`).join("");
    return `<div class="freeze-head"><b>出手瞬间</b><span>定格 ${Math.ceil(left / 1000)} 秒</span></div>
      <p class="freeze-score">${scoreLine(pack, shot)}</p>
      <h3><i class="dot red"></i>${shot.name} #${shot.number}</h3>${offense}
      ${defense ? `<h3><i class="dot gold"></i>防守</h3>${defense}` : ""}`;
  }

  function ellipse(ctx, box, scale, ratio, color, fill, dashed) {
    const [x1, , x2, y2] = box;
    const w = (x2 - x1) * scale;
    const cx = ((x1 + x2) / 2) * scale;
    const cy = y2 * scale;
    const rx = Math.max(w * 0.85, 26 * ratio);
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, rx * 0.3, 0, 0, Math.PI * 2);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    ctx.setLineDash(dashed ? [6 * ratio, 4 * ratio] : []);
    ctx.lineWidth = 3 * ratio;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.restore();
  }

  function tag(ctx, text, x, y, ratio, color) {
    ctx.font = `${Math.round(13 * ratio)}px MiSans, "PingFang SC", sans-serif`;
    const width = ctx.measureText(text).width + 12 * ratio;
    ctx.fillStyle = "rgba(20,17,14,0.9)";
    ctx.fillRect(x - width / 2, y - 20 * ratio, width, 18 * ratio);
    ctx.fillStyle = color;
    ctx.fillRect(x - width / 2, y - 20 * ratio, 3 * ratio, 18 * ratio);
    ctx.fillStyle = "#F3EDE2";
    ctx.textBaseline = "bottom";
    ctx.fillText(text, x - width / 2 + 7 * ratio, y - 4 * ratio);
  }

  function drawMarks(ctx, shot, scale, ratio) {
    const near = shot.nearest;
    if (near?.box) {
      ellipse(ctx, near.box, scale, ratio, "#F5C542", null, true);
      tag(ctx, "最近防守", ((near.box[0] + near.box[2]) / 2) * scale, near.box[1] * scale, ratio, "#F5C542");
    }
    const box = shot.release.shooter_box;
    ctx.save();
    ctx.lineWidth = 2.5 * ratio;
    ctx.strokeStyle = "rgba(255,43,43,0.8)";
    ctx.strokeRect(box[0] * scale, box[1] * scale, (box[2] - box[0]) * scale, (box[3] - box[1]) * scale);
    ctx.restore();
    ellipse(ctx, box, scale, ratio, RED, "rgba(255,43,43,0.32)", false);
    tag(ctx, `出手 · ${shot.name}`, ((box[0] + box[2]) / 2) * scale, box[1] * scale, ratio, RED);
  }

  function heatColor(zone) {
    if (!zone || zone.xfg == null || zone.league_xfg == null) return "rgba(92,81,70,0.35)";
    const diff = Number(zone.xfg) - Number(zone.league_xfg);
    const a = Math.min(0.85, 0.2 + Math.abs(diff) / 0.1 * 0.65);
    return diff >= 0 ? `rgba(241,104,30,${a.toFixed(2)})` : `rgba(0,100,177,${a.toFixed(2)})`;
  }

  function heatmap(shot, highlight) {
    const chart = shot.zoneChart;
    const zones = chart.zones || {};
    const w = 50 * PX;
    const h = 34 * PX;
    const sx = (x) => (25 + x) * PX;
    const sy = (y) => (BASKET_Y + y) * PX;
    const corner = Math.sqrt(23.75 ** 2 - 22 ** 2);
    let cells = "";
    let active = "";
    for (let x = -25; x < 25; x += CELL) {
      for (let y = -BASKET_Y; y < 34 - BASKET_Y; y += CELL) {
        const zone = zoneOf(x + CELL / 2, y + CELL / 2);
        cells += `<rect x="${sx(x)}" y="${sy(y)}" width="${CELL * PX + 0.3}" height="${CELL * PX + 0.3}" fill="${heatColor(zones[zone])}"/>`;
        if (zone === shot.zoneKey) active += `<rect x="${sx(x)}" y="${sy(y)}" width="${CELL * PX + 0.3}" height="${CELL * PX + 0.3}" fill="rgba(255,43,43,0.28)"/>`;
      }
    }
    const labels = Object.entries(ZONE_LABEL_AT).map(([key, [x, y]]) => {
      const zone = zones[key];
      const text = zone && zone.xfg != null ? pct(zone.xfg, 0) : "—";
      return `<text x="${sx(x)}" y="${sy(y)}" class="zone-label">${text}</text>`;
    }).join("");
    const ring = shot.x != null && shot.y != null
      ? `<circle cx="${sx(shot.x)}" cy="${sy(shot.y)}" r="${highlight ? 9 : 7}" fill="rgba(255,43,43,0.35)" stroke="${RED}" stroke-width="${highlight ? 3 : 2.2}"${highlight ? ' class="pulse"' : ""}/>
         <circle cx="${sx(shot.x)}" cy="${sy(shot.y)}" r="2.2" fill="${RED}"/>`
      : "";
    const svg = `<svg viewBox="0 0 ${w} ${h}" class="mini-court heat">
      <rect width="${w}" height="${h}" fill="#2a241c"/>
      ${cells}${active}
      <g fill="none" stroke="#14110E" stroke-width="1.2" opacity="0.75">
        <rect x="${sx(-8)}" y="0" width="${16 * PX}" height="${19 * PX}"/>
        <circle cx="${sx(0)}" cy="${sy(0)}" r="${4 * PX}"/>
        <line x1="${sx(-22)}" y1="0" x2="${sx(-22)}" y2="${sy(corner)}"/>
        <line x1="${sx(22)}" y1="0" x2="${sx(22)}" y2="${sy(corner)}"/>
        <path d="M ${sx(-22)} ${sy(corner)} A ${23.75 * PX} ${23.75 * PX} 0 0 0 ${sx(22)} ${sy(corner)}"/>
      </g>
      <circle cx="${sx(0)}" cy="${sy(0)}" r="3" fill="none" stroke="#F1681E"/>
      ${labels}${ring}
    </svg>`;
    const here = zones[shot.zoneKey];
    const zoneName = ZONE_ZH[shot.zoneKey] || shot.zone || "";
    const mine = here && here.xfg != null
      ? `${shot.name}在${zoneName}平均 ${pct(here.xfg)}（${here.fga} 投），全部球员 ${pct(here.league_xfg)}`
      : `${shot.name}在${zoneName}只有 ${here ? here.fga : 0} 投，样本不足`;
    const caption = `<p class="heat-caption"><b>这一投预期命中率 ${shot.xfg != null ? pct(shot.xfg) : "—"}</b><br>${mine}</p>
      <p class="heat-legend"><i class="sw hot"></i>高于全部球员 <i class="sw cold"></i>低于 <i class="sw none"></i>不足 ${chart.min_shots} 投 · 数字是该区平均预期命中率 · ${chart.games.length} 场</p>`;
    return svg + caption;
  }

  window.DeepFreeze = { FREEZE_MS, candidates, goalTime, cardHtml, drawMarks, heatmap };
})();
