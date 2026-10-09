# 事件流架构（v0.1 提案）

> 来源：10.9 晚 Jason 口述的四段新架构 + 当晚讨论结论。
> 与 architecture.md 的关系：本文是事件流管线的提案稿，A 确认后合并进 architecture.md——机器评分以那份为准。
> 配套代码骨架：`pipeline/events/`（JSON Schema + 四个事件判定模块）。

## 一、两条路径分开

- **评委路径**（提交门户的 CloudFront 链接）：第一屏就是成片回放页，评委只看最终效果，不上传、不等待。
- **生产路径**（内部使用）：上传页 → 管线 → 产出成片。入口藏在回放页后面，不递给评委。

## 二、总图

```plaintext
════════════ 评委路径（提交链接第一屏）════════════
CloudFront ──▶ game.html 成片回放页（成片视频 + 叠加层 + 解说 + 回合面板）
               └── /readme 文字亮点页

════════════ 生产路径（内部）════════════
index.html 上传卡 ──▶ POST /api/jobs ──▶ Lambda create_job ──▶ S3 raw/ 直传
                                            │
                                            ▼
              ┌────────── Bedrock AgentCore（总编排，硬性要求落位）──────────┐
              │                                                            │
              ▼                                                            ▼
     ① track_video                                            ③ enrich_metrics
     Batch g4dn / YOLO+ByteTrack                              官方深度数据按球员+时间轴对齐
     号码 ROI → roster 先验匹配                                自有指标 C = f(官方A, 官方B)
     场地关键点 → 逐帧单应标定                                  口径对齐 glossary，可复算
              │                                                            │
              ▼                                                            ▼
     ② build_structure ──────▶ 结构化文件（中心 artifact）────▶ ④ match_tactics
     事件流引擎                 S3 structured/{jobId}.json      几何谓词出候选
     pipeline/events/          逐事件 + 关键帧采样              Claude 仲裁措辞
                               每条带 confidence / source       低置信退大类（阵地/转换）
              │                                                            │
              ▼                                                            ▼
     ⑤ render_overlay（叠加层 PNG → MediaConvert 成片 MP4 → S3 rendered/）
     ⑥ make_commentary（Claude 解说词 ← 只读结构化文件 → Polly → 混入音轨）
                                            │
                                            ▼
                              DynamoDB：JobsTable 状态机 / EventsTable 事件
                              CloudFront 回放页轮询读取，渲染叠加与面板
```

## 三、四阶段 ↔ AWS 服务映射

| 阶段 | 产出 | AWS 落点 |
| --- | --- | --- |
| ① 感知追踪 | 逐帧位置 + 身份 | Batch（EC2 g4dn + EBS + ECR）、Textract 记分牌校验 |
| ② 结构化 | 结构化文件 JSON | 事件流引擎（Batch 同作业内），落 S3 `structured/` + EventsTable |
| ③ 数据补充 | 官方指标对齐 + 自有指标 C | Lambda 读官方深度数据，按 roster + 记分牌时间轴对齐 |
| ④ 战术识别 | 战术标签 + 置信度 | 几何谓词（代码）+ Bedrock Claude（措辞） |
| ⑤⑥ 呈现 | 成片 + 解说 | MediaConvert、Polly、CloudFront |
| 贯穿 | 编排与降级 | Bedrock AgentCore 决策流程走向；AgentCore 挂则 Lambda 直连各模块 |

## 四、结构化文件（全队的合同）

- Schema 定稿：`pipeline/events/event_stream.schema.json`，Python 对应 `pipeline/events/schema.py`。
- **逐事件 + 关键帧采样**：事件（接球/传球/掩护/出手/篮板/失误）是主线；十人完整站位只在事件前后存快照，渲染时两帧间线性插值。
- 每条记录带 `confidence` 和 `source`（cv / vlm / manual），下游按阈值过滤。
- 每帧标 `calibration`（fresh / stale / none）：none 时段不输出英尺距离，界面这段时间不显示数字。

## 五、降级链（任何时刻都有可提交的东西）

1. **完整模式**：Batch 追踪 + 号码识别 + roster 匹配 → 全量结构化文件。
2. **VLM 降级**：Agent 判断追踪置信度低 → 切 P0 抽帧链路，Qwen3 VL 帧级 JSON 归一化后直接喂事件层（`build_stream.py --mode vlm`）。
3. **保底**：P0 已验证的事件时间线成片。

## 六、镜头与标定策略

- **镜头检测**：帧间直方图突变判切镜 + 木地板色占比筛主机位全景；只在主机位连续段谈距离。
- **逐帧单应**：主机位段内每帧跑场地关键点，≥4 个高置信点解 3×3 H；不足沿用上一帧并标 stale；切镜作废。
- **定性兜底**：标定全挂时距离降级为档位（贴身 / 一步 / 两步开外），口径写进 data-notes.md。

## 七、身份识别

- 号码 ROI + `pipeline/vision/roster.json` 先验匹配（两队激活名单各 ~10 人）。
- 号码撞车已登记在 roster.json `number_collisions`（如 #1 = Amen / Hardy），必须结合队伍色先分队再查表。
- 白天允许人工校正种子帧（`pipeline/vision/review.html`），晚上只换考试场次的 roster 配置。

## 八、战术识别

- 几何谓词出候选（谁向谁收敛、距离变化、掩护角度、防守人换向），Claude 只在候选里仲裁并措辞，不看原始画面。
- 候选动作清单聚焦 15–20 个高频 action：高位/侧翼挡拆、Spain、pick & pop、DHO、zoom、flare、pin down、电梯门、背掩护、backdoor、iso、post-up、转换进攻。
- 置信度不足一律退「阵地进攻 / 转换进攻」大类——大类不错，细分错了才丢人。
