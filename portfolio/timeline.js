// Timeline data is inert text. No imported URLs, scripts or HTML are executed.
export const SAMPLE_ID = "teaching-v1";
export const sampleEvents = [
  { t: 0, label: "回合开始", origin: "合成教学示例", notes: { expert: "A 队 7 号在侧翼持球，12 号位于防守侧。画面仅为示意，不包含真实追踪结果。", new: "亮绿色圆点是持球人，灰色圆点是防守人。先看球在哪里。", together: "一起找找：谁拿着球？哪边是他要进攻的篮筐？" } },
  { t: 3, label: "传球到侧翼", origin: "合成教学示例", notes: { expert: "演示一次传球与落位，未计算真实的空间、引力或防守压力。", new: "把球交给队友叫传球。球在空中移动时，观察接球队员的位置。", together: "为什么不一直自己拿球？可以观察传球后球离篮筐更近还是更远。" } },
  { t: 6, label: "三分出手", origin: "合成教学示例", notes: { expert: "侧翼三分出手。35% 是设定的教学概率，出手价值简化为 3 × 35% = 1.05 分，不含犯规与篮板。", new: "三分线外投进可以得到 3 分。橙色球沿弧线飞向篮筐。", together: "这一球如果投进能得几分？站得远通常更难，但可以多得一分。" } },
  { t: 9, label: "三分命中", origin: "合成教学示例", notes: { expert: "合成回合设定为命中，A 队从 72 分变为 75 分。单次命中不能说明球员长期命中率。", new: "A 队加了 3 分，从领先 2 分变成领先 5 分。", together: "一起算一算：72 加 3 是多少？接下来轮到另一队进攻。" } },
];

export function timeLabel(t) {
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

function text(value, limit, field) {
  if (typeof value !== "string" || !value.trim() || value.length > limit) throw new Error(`${field} 必须是 1–${limit} 字的文本`);
  return value.trim();
}

export function validateTimeline(data, source) {
  if (!data || data.schemaVersion !== 1 || !data.source || !Array.isArray(data.events)) throw new Error("时间线格式无效，需要 schemaVersion: 1、source 和 events");
  const s = data.source;
  if (s.kind !== source.kind || (s.kind === "sample" ? s.id !== source.id : s.name !== source.name || s.size !== source.size)) throw new Error("时间线与当前视频不匹配，请先选择对应视频");
  if (!Number.isFinite(s.duration) || Math.abs(s.duration - source.duration) > 0.25) throw new Error("时间线记录的视频时长与当前视频不一致");
  if (data.events.length > 1000) throw new Error("时间线最多支持 1000 个事件");
  return data.events.map((event) => {
    if (!event || !Number.isFinite(event.t) || event.t < 0 || event.t >= source.duration) throw new Error("事件时间必须位于当前视频范围内");
    const label = text(event.label, 120, "事件名称");
    const notes = {};
    for (const mode of ["expert", "new", "together"]) notes[mode] = text(event.notes?.[mode], 2000, "事件说明");
    return { t: event.t, label, notes, origin: "导入的用户记录（未独立核验）" };
  }).sort((a, b) => a.t - b.t);
}
