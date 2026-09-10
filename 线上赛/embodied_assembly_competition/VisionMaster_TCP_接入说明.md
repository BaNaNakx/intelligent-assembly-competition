# VisionMaster TCP 接入说明

## 当前参数

| 项目 | 当前值 |
| --- | --- |
| VisionMaster 地址 | `127.0.0.1` |
| VisionMaster 端口 | `7930` |
| Python 角色 | TCP 客户端 |
| 任务卡顺序 | 先任务卡 1，后任务卡 2 |
| 任务卡传输格式 | 已保存 PNG/JPG 的绝对本地路径（UTF-8 文本） |
| 图像理解 | 由 Qwen 视觉模型完成，不使用 VisionMaster OCR |

正式入口为：

```powershell
python scripts/run_competition.py
```

在窗口输入 `小具同学`、`执行任务` 后，Python 会在同一条 TCP 连接中先向 VisionMaster 发送请求，再读取对应回复。不是被动等待 VisionMaster 随机推送数据。

两张任务卡均由裁判随机生成。程序先完成任务卡一的识别和文本输出；任务卡一完成后，才解析任务卡二的装配顺序。

## 固定 TCP 请求—响应协议

每个请求使用一条新的 TCP 连接。Python 用 UTF-8 发送请求文本，并以换行符 `\n` 结束；VisionMaster 在同一连接上发送结果。不要为任务卡响应添加 `TASK_CARD_1|` 等自定义前缀。

| Python 发送给 VM | VM 触发的流程 | VM 返回给 Python |
| --- | --- | --- |
| `GET_TASK_CARD_1\n` | 读取并保存任务卡 1 | 任务卡 1 的完整绝对路径，例如 `C:\CompetitionTaskCards\task_card_1.png` |
| `GET_TASK_CARD_2\n` | 读取并保存任务卡 2 | 任务卡 2 的完整绝对路径，例如 `C:\CompetitionTaskCards\task_card_2.png` |

Python 接收一张任务卡 1 并完成 Qwen 文本输出后，才会发送 `GET_TASK_CARD_2`。任务卡 2 解析后，程序只加载内置的固定工位表，不向 VisionMaster 请求坐标。

## VisionMaster 必须完成的配置

保留“通信管理 → `1 TCP服务端`”，不要改为 TCP 客户端。

1. 启用“数据上传”；在接收设置中启用换行结束符，使接收事件可按 `\n` 分割 Python 请求。
2. 在“接收事件”中建立 2 条字符串事件，均绑定 `1 TCP服务端`：`GET_TASK_CARD_1`、`GET_TASK_CARD_2`。
3. 分别把 2 条事件绑定到对应的全局触发/流程：读取任务卡 1、读取任务卡 2。
4. 任务卡流程先将输出图像自动保存到固定文件夹；推荐 `C:\CompetitionTaskCards`。文件名应含 `.jpg`、`.jpeg` 或 `.png` 后缀。
5. 保存完成后，“发送数据”只发送该图片的完整绝对路径；关闭“16进制发送”“结束符”“分隔符”。
6. Python 与 VisionMaster 必须在同一台 Windows 主机运行，或使用两端均可访问且路径相同的共享文件夹。

当前 `图像1.本地原图保存路径` 不是可订阅结果，不能填入“发送数据”。它会使发送节点变红。应发送“保存图像”动作成功后产生的完整文件路径字符串；若 VM 的保存方式是脚本，则脚本输出该完整路径字符串。

## 固定工位数据

六个物料位和六个托盘位属于任务书规定的静态数据。它们已内置到 Python 程序包中，比赛电脑不需要部署项目书文件；VisionMaster 不发送也不更新这些坐标。

## 仍需做的一次联调

用 Python 入口依次触发任务卡 1、任务卡 2 流程。请保留一次真实图片路径回应，用于确认 VM 实际的连接关闭和换行行为。Python 会检查路径是否绝对、文件是否存在、后缀是否为 JPG/JPEG/PNG，以及图片内容与大小是否有效。
