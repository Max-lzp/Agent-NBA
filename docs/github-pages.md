# 发布作品集到 GitHub Pages

## 发布范围

`portfolio/` 是独立、无构建依赖的静态站点，包含产品介绍、三种观看模式的合成回合交互、技术方案和项目局限。所有站内资源使用相对路径，适配 GitHub Pages 的仓库子路径。

只上传 `portfolio/`，不上传 `web/`、比赛视频、官方原始数据、账号配置或后端服务。仓库 `docs/LICENSING.md` 限定比赛录像仅用于黑客松评审；在取得公开展示许可前，不将比赛录像拷入此目录。

页面明确标注合成数据与预写文案，不伪装成实时 AI 输出。原上传、问答、云端视频分析与原播放器未迁移到此站点。后续恢复这些功能需要可用的后端。

## 本地预览

在仓库根目录运行：

```bash
python3 -m http.server 8000 --directory .
```

本地检查 `/portfolio/` 页面和三种模式切换。无需 npm、AWS 凭证或模型权重。

## GitHub 发布

1. 将 `portfolio/` 与 `.github/workflows/pages.yml` 提交到 GitHub 仓库的 `main` 分支。
2. 打开仓库 Settings → Pages，选择 Build and deployment → Source → GitHub Actions。
3. 打开 Actions → Publish portfolio to GitHub Pages，必要时手动 Run workflow，目标分支选 `main`。
4. 等待任务成功，以部署任务返回的 `page_url` 为准。

默认仓库地址预计为 `https://max-lzp.github.io/Agent-NBA/`，部署成功前不视为已上线。私有仓库能否启用 Pages 取决于账户套餐及组织策略；本流程不修改仓库可见性。若不支持，可新建公开的作品集仓库，仅复制 `portfolio/` 的公开内容，并采用相同的 Pages 发布方式。

网站发布成功后，验证首页、手机宽度、模式切换和源码链接。页面公开可见范围以 Pages 设置为准。
