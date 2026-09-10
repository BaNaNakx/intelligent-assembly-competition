# ARCS 与 VMware 赛前配置说明

## 已验证的开发环境

| 项目 | 值 |
| --- | --- |
| 宿主机 IP | `192.168.1.8` |
| ARCS 虚拟机 IP | `192.168.1.16` |
| ARCS JSON-RPC 端口 | `30004` |
| 机器人名称 | `rob1` |
| VMware 网络模式 | 桥接 |

宿主机已成功通过 JSON-RPC 连接 `rob1`，读取状态并执行两次完整任务卡二六步仿真。每步均无碰撞并返回比赛规定初始位姿。

## 比赛电脑设置

比赛前启动 VMware 和 ARCS，并确认：

1. 虚拟机 IP 可从宿主机访问；
2. `30004` 可连接；
3. ARCS 中已加载 `rob1` 并在执行前上电；
4. 在一键比赛窗口填入最终 ARCS 虚拟机 IP 与端口。

ARCS 机器人控制使用 `30004`，不是界面中显示的 `30000`。

可用以下命令只读检查连接：

```powershell
python scripts/check_arcs_connection.py
```

不需要 ARCS Python 插件或 SDK。程序直接使用 ARCS JSON-RPC。
