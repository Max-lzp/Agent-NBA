# 发布作品集到 GitHub Pages

## 发布范围

网站首页现已恢复原比赛版播放器。`scripts/build_archive_site.py` 复用 `web/` 的页面、主题和交互，配合已核对的弗拉格成片生成独立静态站点；`portfolio/` 作为辅助案例说明和本地标记工具放在 `case-study/` 子路径。详见 [restored-competition-replay.md](restored-competition-replay.md)。

只上传构建器生成的目录，不直接上传整个仓库。用户已明确确认仓库保留片段可公开展示，本次包含已核对的 `flagg-replay.mp4`。不包含账号配置、凭证、上传后台或运维发布台。

首页使用真实成片和原分析快照，明确标注离线归档能力；辅助工具保留合成教学片。二者都不伪装成实时 AI 输出。上传分析和实时大模型问答仍需后续实现。

## 本地预览

在仓库根目录运行：

```bash
python3 scripts/build_archive_site.py --output /tmp/agent-nba-site
python3 -m http.server 8000 --directory /tmp/agent-nba-site
```

本地检查根页面和三种模式切换。无需 npm、AWS 凭证或模型权重。

## GitHub 发布

1. 将 `portfolio/` 与 `.github/workflows/pages.yml` 提交到 GitHub 仓库的 `main` 分支。
2. 打开仓库 Settings → Pages，选择 Build and deployment → Source → GitHub Actions。
3. 打开 Actions → Publish portfolio to GitHub Pages，必要时手动 Run workflow，目标分支选 `main`。
4. 等待任务成功，以部署任务返回的 `page_url` 为准。

默认仓库地址预计为 `https://max-lzp.github.io/Agent-NBA/`，部署成功前不视为已上线。私有仓库能否启用 Pages 取决于账户套餐及组织策略；本流程不修改仓库可见性。若不支持，可新建公开的作品集仓库，仅复制 `portfolio/` 的公开内容，并采用相同的 Pages 发布方式。

网站发布成功后，验证首页、手机宽度、模式切换和源码链接。页面公开可见范围以 Pages 设置为准。
