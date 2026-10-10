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

  const OFF_RANKS = [["pts", "场均得分", 1], ["fg_pct", "投篮命中率", "pct"], ["ts_pct", "真实命中率", "pct"]];
  const DEF_RANKS = [["stl", "场均抢断", 1], ["blk", "场均盖帽", 1], ["dreb", "场均防守篮板", 1], ["def_rating", "防守效率", 1]];

  function rankLines(ranks, spec) {
    return spec.filter(([key]) => ranks && ranks[key]).map(([key, label, fmt]) => {
      const r = ranks[key];
      const value = fmt === "pct" ? pct(r.value) : Number(r.value).toFixed(fmt);
      return `<p class="rank"><span>${label} ${value} · 队内第 ${r.rank}/${r.of}</span>`
        + `<i class="stars" style="--s:${r.stars}" aria-label="${r.stars} 星"></i></p>`;
    });
  }

  function offenseLines(shot) {
    const lines = rankLines(shot.shooterRank, OFF_RANKS);
    const s = shot.season;
    if (s && !lines.length) lines.push(`赛季场均 ${s.pts} 分 · 投篮 ${pct(s.fg_pct)} · 三分 ${pct(s.fg3_pct)}`);
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
    const d = shot.defender;
    if (!d || !d.name) {
      const team = d?.team ? `${TEAM_ZH[d.team] || d.team}球员，` : "";
      return [`领防：${team}号码没读出`];
    }
    const who = d.display || d.name;
    const lines = [`领防：${who} #${d.number}（画面，出手前贴防 ${Number(d.guard_seconds).toFixed(1)} 秒）`];
    const vs = d.vs_shooter;
    if (vs) lines.push(`本场对位 ${Math.round(vs.seconds / 60)} 分钟，${shot.name} ${vs.fgm}/${vs.fga}、得 ${vs.points} 分`);
    const ranked = rankLines(d.team_rank, DEF_RANKS);
    lines.push(...ranked);
    if (d.season && !ranked.length) lines.push(`赛季场均 抢断 ${d.season.stl} · 盖帽 ${d.season.blk} · 防守篮板 ${d.season.dreb}`);
    return lines;
  }

  function shooterSource(shot) {
    const c = shot.shooterCheck;
    if (c && c.number && c.number === c.official_number) return "画面确认";
    if (c && c.number && c.number !== c.official_number) return `画面读到 #${c.number}，以官方记录为准`;
    return "官方记录";
  }

  const POS_ZH = {
    Guard: "后卫", Forward: "前锋", Center: "中锋",
    "Guard-Forward": "后卫/前锋", "Forward-Guard": "前锋/后卫",
    "Forward-Center": "前锋/中锋", "Center-Forward": "中锋/前锋",
  };

  function formatPct(value) {
    if (value === null || value === undefined || value === "") return "";
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return `${(number * 100).toFixed(1)}%`;
  }

  function attempts(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function shownPct(pct, fga) {
    const taken = attempts(fga);
    if (taken === null || taken <= 0) return "";
    return formatPct(pct);
  }

  function countText(fgm, fga) {
    const makes = attempts(fgm);
    const taken = attempts(fga);
    if (makes === null || taken === null || taken <= 0) return "";
    const trim = (number) => (Number.isInteger(number) ? String(number) : String(number));
    return `${trim(makes)}/${trim(taken)}`;
  }

  function bandOf(shot) {
    const zone = shot.zoneKey || shot.shot_zone || "";
    const threes = { top3: 1, corner3_l: 1, corner3_r: 1, wing3_l: 1, wing3_r: 1 };
    const twos = { restricted: 1, paint: 1, midrange: 1 };
    const value = attempts(shot.value);
    if (value === 3 || threes[zone]) return "three";
    const distance = attempts(shot.distance != null ? shot.distance : shot.distance_ft);
    if (distance !== null) {
      if (distance < 6) return "near";
      if (value === 2 || twos[zone]) return "other_two";
      return null;
    }
    if (zone === "restricted") return "near";
    if (zone === "midrange") return "other_two";
    return null;
  }

  function cmText(value) {
    const number = attempts(value);
    if (number === null || number <= 0) return "";
    return `${number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")} cm`;
  }

  function positiveInt(value) {
    const number = attempts(value);
    return Number.isInteger(number) && number > 0 ? number : null;
  }

  function draftText(profile) {
    if (!profile) return "";
    if (profile.draft_status === "undrafted") return "落选";
    const year = positiveInt(profile.draft_year);
    const round = positiveInt(profile.draft_round);
    const pick = positiveInt(profile.draft_overall_pick);
    let text = year ? `${year}年` : "";
    if (round && pick) text += `第${round}轮第${pick}顺位`;
    else if (round) text += `第${round}轮`;
    else if (pick) text += `第${pick}顺位`;
    return text;
  }

  function profileLine(profile) {
    if (!profile) return "";
    const parts = [];
    const feet = profile.height_ft_in || "";
    const cm = cmText(profile.height_cm);
    if (feet && cm) parts.push(`身高 ${feet}（${cm}）`);
    else if (feet) parts.push(`身高 ${feet}`);
    else if (cm) parts.push(`身高 ${cm}`);
    const position = profile.position_zh || POS_ZH[profile.position] || "";
    if (position) parts.push(position);
    const draft = draftText(profile);
    if (draft) parts.push(draft);
    const span = cmText(profile.wingspan_cm);
    if (span) parts.push(`臂展 ${span}`);
    return parts.join(" · ");
  }

  function diffPhrase(band, row) {
    if (band === "other_two" || !row) return "";
    const diff = row.diff_from_opponents_normal_official;
    if (diff === null || diff === undefined || diff === "") return "";
    const number = Number(diff);
    if (!Number.isFinite(number)) return "";
    const points = Math.abs(number * 100);
    const normal = formatPct(row.opponents_normal_fg_pct_official);
    const direction = number < 0 ? "低" : "高";
    const base = normal ? `平时 ${normal}` : "平时";
    if (points.toFixed(1) === "0.0") return `对手在他面前与${base}持平`;
    if (normal) return `对手在他面前比平时 ${normal} ${direction} ${points.toFixed(1)} 个百分点`;
    return `对手在他面前比平时${direction} ${points.toFixed(1)} 个百分点`;
  }

  function zoneHtml(zone) {
    if (!zone || !zone.band) return "";
    const bands = zone.defender_bands || [];
    let html = "";
    if (bands.length) {
      const cells = bands.map((band) => {
        const pct = shownPct(band.opponent_fg_pct, band.opponent_fga);
        const count = countText(band.opponent_fgm, band.opponent_fga);
        const zero = attempts(band.opponent_fga) === 0;
        const value = pct || (zero ? "无出手" : "");
        const on = band.band === zone.band ? "is-shot" : "";
        const sub = [count, band.band === zone.band ? "这一投" : ""].filter(Boolean).join(" · ");
        return `<span class="${on}"><b>${band.label}</b>${value}${sub ? `<small>${sub}</small>` : ""}</span>`;
      }).join("");
      html += `<p class="zone-note">对手命中率，越低越好</p><div class="bands">${cells}</div>`;
    }
    const current = zone.defender || {};
    const pct = shownPct(current.opponent_fg_pct, current.opponent_fga);
    const count = countText(current.opponent_fgm, current.opponent_fga);
    if (!bands.length && current.player_id && !pct && !count) {
      html += `<p>这一投${zone.label || ""}没有可用的对手命中率</p>`;
    }
    const phrase = diffPhrase(zone.band, current);
    if (phrase) html += `<p class="zone-diff">${phrase}</p>`;
    const shooter = zone.shooter || {};
    const shooterPct = shownPct(shooter.fg_pct, shooter.fga);
    const shooterCount = countText(shooter.fgm, shooter.fga);
    if (shooterPct || shooterCount) {
      html += `<p>投篮人同区${zone.label || ""}${shooterPct ? ` ${shooterPct}` : ""}${shooterCount ? `（${shooterCount}）` : ""}</p>`;
    }
    return html;
  }

  function cardHtml(pack, shot, left) {
    const wrap = (line) => (line.startsWith("<p") ? line : `<p>${line}</p>`);
    const offense = offenseLines(shot).map(wrap).join("");
    const defense = defenseLines(shot);
    const lead = defense.slice(0, 1).map(wrap).join("");
    const rest = defense.slice(1).map(wrap).join("");
    const shooterBio = profileLine(shot.shooterProfile);
    const defenderBio = profileLine(shot.defender && shot.defender.profile);
    return `<div class="freeze-head"><b>出手瞬间</b><span>定格 ${Math.ceil(left / 1000)} 秒</span></div>
      <h3><i class="dot red"></i>${shot.name} #${shot.number}<small>${shooterSource(shot)}</small></h3>${shooterBio ? `<p class="bio">${shooterBio}</p>` : ""}${offense}
      <h3><i class="dot gold"></i>防守</h3>${lead}${defenderBio ? `<p class="bio">${defenderBio}</p>` : ""}${rest}${zoneHtml(shot.seasonZone)}`;
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

  function sideTag(ctx, text, box, scale, ratio, right, color) {
    ctx.font = `${Math.round(13 * ratio)}px MiSans, "PingFang SC", sans-serif`;
    const width = ctx.measureText(text).width + 12 * ratio;
    const x = right ? box[2] * scale + 5 * ratio : box[0] * scale - width - 5 * ratio;
    const y = (box[1] + (box[3] - box[1]) * 0.45) * scale - 9 * ratio;
    ctx.fillStyle = "rgba(20,17,14,0.9)";
    ctx.fillRect(x, y, width, 18 * ratio);
    ctx.fillStyle = color;
    ctx.fillRect(right ? x : x + width - 3 * ratio, y, 3 * ratio, 18 * ratio);
    ctx.fillStyle = "#F3EDE2";
    ctx.textBaseline = "bottom";
    ctx.fillText(text, x + 6 * ratio, y + 15 * ratio);
  }

  function drawMarks(ctx, shot, scale, ratio) {
    const lead = shot.leadBox;
    const box = shot.release.shooter_box;
    if (lead) {
      ellipse(ctx, lead, scale, ratio, "#F5C542", null, true);
      const d = shot.defender;
      const right = lead[0] + lead[2] >= box[0] + box[2];
      sideTag(ctx, d?.name ? `领防 · ${d.display || d.name} #${d.number}` : "领防", lead, scale, ratio, right, "#F5C542");
    }
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

  window.DeepFreeze = {
    FREEZE_MS, candidates, goalTime, cardHtml, drawMarks, heatmap,
    formatPct, shownPct, bandOf, profileLine, zoneHtml, diffPhrase,
  };
})();
