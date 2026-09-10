# 智能装配大赛

全国大学生具身智能精密装配大赛项目的公开源码与双电脑任务上下文同步仓库。

## 目录

- `线上赛/`：线上赛源码和测试。
- `线下赛/决赛代码/`：当前主要维护的决赛代码。
- `线下赛/所需文件/`：用于开发与测试的任务卡示例图。
- `线下赛测试/`：独立机械臂对准测试。
- `context/`：跨电脑、跨 Codex 任务延续所需的脱敏项目状态。
- `.agents/skills/intelligent-assembly-sync/`：项目专用同步 Skill。

## 隐私边界

公开仓库不包含 API 密钥、个人信息、原始 Codex 对话、本机截图、本机运行参数、历史压缩包、Office/PDF 资料或第三方安装包。完整原始资料只保存在本机离线迁移包中；需要公开的新文件必须先做敏感信息检查。

## 当前主代码验证

```powershell
cd 线下赛\决赛代码
python -m pip install -e .
python -m unittest
```

## 双电脑工作方式

开始工作时说：

```text
使用 intelligent-assembly-sync 拉取最新进度
```

完成工作并准备同步时说：

```text
使用 intelligent-assembly-sync 保存并推送本次进度
```

完整规则见 [context/SYNC_GUIDE.md](context/SYNC_GUIDE.md)。
