# PC/ARM 语音服务 HTTP/WS 部署实施测试文档

## 1. 目标与范围

本文档用于 M28-3 之后的 `arm_real` 语音通讯部署测试，先在 PC 本机部署 `tools/arm_speech_service` mock 服务，验证 PC 主控通过 HTTP + WebSocket 调用语音服务的链路；PC 本机验证稳定后，再由操作员手动部署到 ARM 端侧环境，并使用 `--provider real` 接入 ARM 本机真实唤醒、VAD、录音、ASR 与 TTS。

边界固定如下：

- PC 主控负责 Qt GUI、CLI、AgentGateway、TaskJSON、编排执行和证据汇总。
- ARM/语音服务只负责唤醒、VAD、录音、ASR、TTS 播放和音频资源管理。
- ARM/语音服务不得生成 TaskJSON、不得控制机器人、不得写 PC 主证据文件。
- `arm_real` 服务不可用时只允许回退 `--speech-asr-text/--speech-asr-text-file` 文本覆盖；不得自动启用 PC 本地 `real` 麦克风链路。
- 真 ARM 板端联调完成前，不把 `real ARM passed` 写入正式验收记录。

## 2. 通讯拓扑

PC 本地验证：

```text
Qt GUI / baseline CLI
  -> SpeechAdapter(arm_real)
  -> http://127.0.0.1:8765
     - GET  /health
     - POST /v1/asr
     - POST /v1/tts
     - POST /v1/tts/cancel
     - WS   /v1/events
```

ARM 局域网联调：

```text
PC Qt GUI / baseline CLI
  -> SpeechAdapter(arm_real)
  -> http://<arm-ip>:8765
     - GET  /health
     - POST /v1/asr
     - POST /v1/tts
     - POST /v1/tts/cancel
     - WS   ws://<arm-ip>:8765/v1/events
```

默认端口为 `8765`。如现场端口被占用，可换端口，但 PC CLI/Qt URL 必须同步调整。

## 3. PC 本地部署验证

### 3.1 准备环境

在仓库根目录执行：

```bash
bash scripts/init_env.sh
source .venv/bin/activate
```

如果当前 shell 配置了代理，建议本地验证时加上：

```bash
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
```

说明：`SpeechAdapter(arm_real)` 的 HTTP client 已禁用系统代理，避免局域网/本机请求被代理劫持；但手工 `curl` 命令仍可能受 `ALL_PROXY/HTTP_PROXY` 影响，所以建议显式设置 `NO_PROXY`，或在 `curl` 中使用 `--noproxy '*'`。

### 3.2 启动 PC 本地 mock 语音服务

终端 A：

```bash
source .venv/bin/activate
python3 tools/arm_speech_service/server.py --host 127.0.0.1 --port 8765
```

等价显式写法：

```bash
python3 tools/arm_speech_service/server.py --provider mock --host 127.0.0.1 --port 8765
```

预期输出：

```text
ARM speech mock listening on http://127.0.0.1:8765
```

若要让局域网其他机器访问 PC 上的 mock 服务：

```bash
python3 tools/arm_speech_service/server.py --provider mock --host 0.0.0.0 --port 8765
```

### 3.3 HTTP health 验证

终端 B：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8765/health | jq .
```

预期关键字段：

```json
{
  "ready": true,
  "service": "arm_speech_service",
  "version": "m28-3-mock",
  "provider": "mock"
}
```

### 3.4 HTTP ASR 验证

```bash
curl --noproxy '*' -sS \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"pc-local-asr-001","mode":"live_capture","wakeup_required":true,"language":"zh-CN"}' \
  http://127.0.0.1:8765/v1/asr | jq .
```

预期关键字段：

- `ok=true`
- `error_code=OK`
- `result_digest.instruction=把红色方块放到左托盘`
- `result_digest.asr_source=arm_speech_service`
- `result_digest.input_mode=live_capture`

### 3.5 HTTP TTS 与 cancel 验证

```bash
curl --noproxy '*' -sS \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"pc-local-tts-001","text":"mock 播报","backend":"piper","voice":"default"}' \
  http://127.0.0.1:8765/v1/tts | jq .
```

预期关键字段：

- `ok=true`
- `result_digest.spoken_text=mock 播报`
- `result_digest.tts_source=arm_speech_service`

取消播报：

```bash
curl --noproxy '*' -sS \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"pc-local-cancel-001","cancel_source":"pc_manual"}' \
  http://127.0.0.1:8765/v1/tts/cancel | jq .
```

预期关键字段：

- `ok=true`
- `message=tts cancel accepted`

### 3.6 PC CLI `arm_real` 主链验证

```bash
rm -rf /tmp/aubo_m28_arm_real_pc_local
source .venv/bin/activate
python3 -m aubo_app.baseline.cli \
  --scenario success \
  --speech-transport arm_real \
  --arm-speech-base-url http://127.0.0.1:8765 \
  --arm-speech-timeout-s 1 \
  --speech-asr-enable \
  --speech-tts-enable \
  --speech-tts-text 'mock 播报' \
  --output-dir /tmp/aubo_m28_arm_real_pc_local
```

预期：

- CLI 退出码为 `0`。
- 输出摘要中：
  - `speech_transport=arm_real`
  - `asr_source=arm_speech_service`
  - `tts_source=arm_speech_service`
  - `final_status=TASK_DONE`
  - `error_code=OK`
- 终端 A 服务日志至少出现：
  - `POST /v1/asr`
  - `GET /v1/events`
  - `POST /v1/tts`
  - `POST /v1/tts/cancel`（mock events 会触发一次 wakeup 打断路径）

证据文件检查：

```bash
jq '.final_status, .error_code, .asr_source, .tts_source, .speech_asr_digest, .speech_tts_digest' \
  /tmp/aubo_m28_arm_real_pc_local/report.json
```

### 3.7 服务不可用 text fallback 验证

此项验证 `arm_real` 不会自动启用 PC 本地 real 麦克风链路。

```bash
rm -rf /tmp/aubo_m28_arm_real_text_fallback
source .venv/bin/activate
python3 -m aubo_app.baseline.cli \
  --scenario success \
  --speech-transport arm_real \
  --arm-speech-base-url http://127.0.0.1:9 \
  --arm-speech-timeout-s 0.01 \
  --speech-asr-enable \
  --speech-asr-text '把红色方块放到左托盘' \
  --speech-tts-enable \
  --speech-tts-text '测试 ARM 服务不可用回退' \
  --output-dir /tmp/aubo_m28_arm_real_text_fallback
```

预期：

- CLI 退出码为 `0`，任务仍可完成。
- `report.json` 中：
  - `speech_asr_digest.input_mode=text_override`
  - `speech_asr_digest.speech_fallback_from=arm_speech_service`
  - `speech_asr_digest.fallback_error_code=SPEECH_SERVICE_UNAVAILABLE`
  - `speech_tts_digest.tts_skipped=true`
  - `speech_tts_digest.skip_code=SPEECH_SERVICE_UNAVAILABLE`

验证命令：

```bash
jq '.speech_asr_digest.input_mode,
    .speech_asr_digest.speech_fallback_from,
    .speech_asr_digest.fallback_error_code,
    .speech_tts_digest.tts_skipped,
    .speech_tts_digest.skip_code' \
  /tmp/aubo_m28_arm_real_text_fallback/report.json
```

### 3.8 Qt GUI 本地验证

启动 Qt GUI：

```bash
source .venv/bin/activate
python3 -m aubo_app.qt_gui.app
```

界面配置：

- `scenario=competition_real`
- `speech=arm_real`
- 勾选 `ASR`
- 需要播报时勾选 `TTS`
- `ARM speech URL=http://127.0.0.1:8765`
- `events` 留空即可，系统默认推导为 `ws://127.0.0.1:8765/v1/events`
- `timeout=5.0` 或现场需要的秒数

点击“启动”前会出现 real transport 安全确认；确认后观察：

- GUI 日志出现 `speech_status`、`round_start/round_done` 或任务状态刷新。
- 服务端日志出现 `/v1/asr`、`/v1/events`、`/v1/tts`。
- 导出证据包后，`report.json` 仍只使用统一 `SpeechAdapter` digest 字段。

## 4. ARM 端部署联调

### 4.1 部署文件

将以下目录及其依赖的 `aubo_app/adapters/speech`、`MODELS/asr`、TTS 模型资源部署到 ARM，并从仓库根目录启动服务：

```text
tools/arm_speech_service/
aubo_app/adapters/speech/
MODELS/asr/
MODELS/tts/  # 使用 Piper 默认模型时需要
```

最小启动命令：

```bash
bash scripts/init_env.sh
source .venv/bin/activate
python3 tools/arm_speech_service/server.py --provider real --host 0.0.0.0 --port 8765
```

真实 ARM provider 已通过 `--provider real` 复用仓库现有本地真实语音链路；若现场后续替换为其他内部实现，仍需保持外部 HTTP/WS 协议不变：

- `health()` 返回真实服务版本、模型状态、音频设备状态。
- `asr(payload)` 接入真实唤醒/VAD/录音/ASR。
- `tts(payload)` 接入真实 TTS 播放。
- `cancel_tts()` 停止当前播报。
- `/v1/events` 推送真实 `wakeup.detected` 与 TTS/ASR 状态事件。

### 4.1.1 ARM 真实 provider 环境检查

`--provider real` 启动前至少确认：

- `AUBO_SPEECH_ASR_MODEL_DIR` 指向 Qwen3SherpaASR 模型目录；默认是仓库根目录下 `MODELS/asr`。
- `AUBO_SPEECH_KEYWORD_MODEL` 与 `AUBO_SPEECH_VAD_MODEL` 默认使用 `aubo_app/adapters/speech/resources/` 内资源；若 ARM 路径不同需显式设置。
- `AUBO_SPEECH_TTS_BACKEND=piper` 或 `edge_tts`；Piper 默认查找 `MODELS/tts`，必要时设置 `AUBO_SPEECH_TTS_PIPER_MODEL` 与 `AUBO_SPEECH_TTS_PIPER_CONFIG`。
- ARM 麦克风、扬声器和播放器可用；播放器可通过 `AUBO_SPEECH_TTS_PLAYER` 指定。
- `sounddevice`、`sherpa-onnx`、`piper` 或 `edge_tts` 等真实链路依赖已在 `.venv` 中安装。

缺失依赖时可直接执行：

```bash
source .venv/bin/activate
uv pip install sounddevice sherpa-onnx piper-tts edge-tts webrtcvad-wheels numpy
```

### 4.2 网络检查

在 PC 上确认 ARM 可达：

```bash
ping <arm-ip>
curl --noproxy '*' -sS http://<arm-ip>:8765/health | jq .
```

`--provider real` 的 `/health` 应返回 `provider=real`。若 `ready=false`，优先根据 `error_code/message` 修复模型路径、音频设备或依赖安装。

如失败，先排查：

- PC 与 ARM 是否在同一局域网。
- ARM 防火墙是否放行 `8765/tcp`。
- ARM 服务是否绑定 `0.0.0.0`，而不是只绑定 `127.0.0.1`。
- PC shell 代理是否劫持局域网请求，必要时设置 `NO_PROXY=<arm-ip>,127.0.0.1,localhost`。

### 4.3 PC CLI 指向 ARM

```bash
rm -rf /tmp/aubo_m28_arm_real_arm_lan
source .venv/bin/activate
python3 -m aubo_app.baseline.cli \
  --scenario success \
  --speech-transport arm_real \
  --arm-speech-base-url http://<arm-ip>:8765 \
  --arm-speech-events-url ws://<arm-ip>:8765/v1/events \
  --arm-speech-timeout-s 5 \
  --speech-asr-enable \
  --speech-tts-enable \
  --speech-tts-text 'ARM 端语音服务联调播报' \
  --output-dir /tmp/aubo_m28_arm_real_arm_lan
```

通过条件：

- CLI 退出码为 `0`。
- ARM 服务日志显示 HTTP/WS 请求。
- PC `report.json` 中 `asr_source/tts_source` 为 `arm_speech_service`。
- 如果现场故意关闭 ARM 服务并提供 `--speech-asr-text`，PC 证据中应出现 `SPEECH_SERVICE_UNAVAILABLE` 和 text fallback 字段。

## 5. 失败分型与回退边界

| 现象 | 期望分型 | 处理 |
|---|---|---|
| `/health` 不通 | `SPEECH_SERVICE_UNAVAILABLE` | 检查服务、IP、端口、防火墙、代理 |
| `/v1/asr` 超时且无文本兜底 | `SPEECH_SERVICE_UNAVAILABLE` | 不启动 PC 本地 real 麦克风，现场修 ARM 服务 |
| `/v1/asr` 超时但有文本兜底 | text override 成功 | digest 写 `speech_fallback_from/fallback_error_code` |
| `/v1/tts` 超时 | TTS skipped | 主流程不因播报失败中断 |
| `/v1/events` 断连 | 唤醒打断能力不可用 | ASR/TTS HTTP 仍可单独验证，现场需修 WS |
| ARM 返回非 JSON | `SPEECH_SERVICE_BAD_RESPONSE` | 修 ARM 服务响应格式 |

## 6. 验收记录边界

PC 本地 mock 验证通过后，可记录为“PC 侧 arm_real HTTP/WS 通讯链路通过”。只有满足以下条件后，才可记录真实 ARM 联调通过：

- ARM 端服务实际部署在端侧设备。
- PC 通过 `<arm-ip>` 完成 `/health`、`/v1/asr`、`/v1/tts`、`/v1/tts/cancel`、`/v1/events` 验证。
- Qt GUI 或 CLI 产出的证据目录可追溯。
- 记录中明确未包含 real VM/real_robot，除非同轮也完成对应门禁验证。
