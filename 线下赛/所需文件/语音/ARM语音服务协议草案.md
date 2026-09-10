# ARM 语音服务协议草案

## 1. 定位

本文档定义后续 ARM 开发板端语音服务与 PC 主控之间的最小协议草案。当前阶段只冻结语义和证据口径，不部署 ARM 服务，不替换现有 PC 端 `aubo_app/adapters/speech` 实现。

目标是让未来 `SpeechAdapter` 可以把本地 real provider 替换为 HTTP/WS/IPC transport，同时保持上层 ASR/TTS 结果语义不变。

M28-1 已在 PC 端新增 `speech-transport=arm_fake` 作为协议 fake 验证入口；该入口只模拟本协议映射，不表示真实 ARM 服务已部署。

M28-3 在 PC 端新增 `speech-transport=arm_real`，通过局域网 HTTP + WebSocket 调用 ARM 语音服务。当前仓库同时提供 `tools/arm_speech_service/` 最小 mock 服务骨架，用于 PC 本机 real transport 联调与后续 ARM 端手动部署起点；真 ARM 环境未手工验收前，不宣称 real ARM 已通过。

M28-4 已为 `tools/arm_speech_service/server.py` 增加 `--provider mock|real`。`mock` 继续用于 PC 本机通讯验证；`real` 复用仓库现有本地真实语音链路（唤醒、VAD、录音、Qwen3SherpaASR、Piper/Edge TTS）并保持本协议对外字段不变。真实 ARM 音频硬件仍需用户手工部署和局域网联调补证后，才可登记为 real ARM 通过。

部署实施与测试步骤见 `docs/PC_ARM语音服务HTTP_WS部署实施测试文档.md`，当前建议先在 PC 本地启动 mock 服务完成 HTTP/WS 通讯验证，再迁移到 ARM 局域网联调。

## 2. 非目标

- 不新增任务调度入口。
- 不让 ARM 服务直接触发机器人动作。
- 不改变 `TaskJSON + JSONL/Event` 主链。
- 不绑定具体模型内部结构、音频库实现或 ARM 镜像版本。
- 不要求本阶段完成真实 ARM 硬件验证。

## 3. 职责边界

| 部署位置 | 职责 | 禁止事项 |
|---|---|---|
| ARM 语音服务 | 唤醒、VAD、录音、ASR、TTS 播放、音频资源管理 | 不生成 TaskJSON，不控制机器人，不写主证据文件 |
| PC 主控 | GUI、AgentGateway、TaskJSON、编排执行、证据汇总 | 不依赖 ARM 内部模型细节 |
| `SpeechAdapter` | 统一本地/远端语音结果语义 | 不暴露多套 ASR/TTS 字段口径 |

## 4. 事件语义

ARM 服务应至少支持以下事件名称：

| 事件 | 触发时机 | 关键字段 |
|---|---|---|
| `wakeup.detected` | 识别到“小E同学” | `session_id/wakeup_source/timestamp` |
| `asr.partial` | 可选，流式中间文本 | `session_id/text/is_final=false` |
| `asr.final` | 语音识别完成 | `session_id/text/input_mode/record_seconds/sample_rate` |
| `asr.failed` | 唤醒、录音、VAD 或识别失败 | `session_id/error_code/message/retryable` |
| `tts.started` | 播报开始 | `session_id/text/backend/voice` |
| `tts.finished` | 播报正常结束 | `session_id/text/backend/voice/duration_ms` |
| `tts.cancelled` | 播报被唤醒、停止或关闭打断 | `session_id/text/cancel_source` |

事件时间戳统一使用 ISO 8601 字符串；事件体必须可 JSON 序列化。

## 5. 请求响应草案

### 5.0 服务发现与事件通道

- `GET /health`：返回服务就绪状态、服务名、版本与 provider 摘要。
- `WS /v1/events`：推送 `wakeup.detected/asr.partial/asr.final/asr.failed/tts.*` 事件。
- PC CLI 参数：
  - `--speech-transport arm_real`
  - `--arm-speech-base-url`，默认 `AUBO_ARM_SPEECH_BASE_URL` 或 `http://127.0.0.1:8765`
  - `--arm-speech-events-url`，默认由 base URL 推导为 `ws://host/v1/events`
  - `--arm-speech-timeout-s`，默认 `5.0`

### 5.1 ASR 请求

```json
{
  "request_id": "uuid",
  "mode": "live_capture",
  "wakeup_required": true,
  "start_timeout_s": 5.0,
  "max_record_seconds": 10.0,
  "vad_threshold": 0.5,
  "language": "zh-CN"
}
```

### 5.2 ASR 成功响应

```json
{
  "ok": true,
  "error_code": "OK",
  "message": "asr ok",
  "result_digest": {
    "tool_name": "asr",
    "summary": "asr ok",
    "instruction": "把红色方块放到左托盘",
    "asr_source": "arm_speech_service",
    "input_mode": "live_capture",
    "wakeup_source": "azure_keyword",
    "sample_rate": 16000,
    "record_seconds": 2.34
  }
}
```

### 5.3 ASR 失败响应

```json
{
  "ok": false,
  "error_code": "ASR_TIMEOUT",
  "message": "no valid speech detected before timeout",
  "retryable": true,
  "result_digest": {
    "tool_name": "asr",
    "summary": "no valid speech detected before timeout",
    "asr_source": "arm_speech_service",
    "input_mode": "live_capture"
  }
}
```

### 5.4 TTS 请求

```json
{
  "request_id": "uuid",
  "text": "场景识别完成，等待任务指令",
  "backend": "piper",
  "voice": "default",
  "interrupt_policy": "cancel_on_wakeup"
}
```

### 5.5 TTS 响应

```json
{
  "ok": true,
  "error_code": "OK",
  "message": "tts ok",
  "result_digest": {
    "tool_name": "tts",
    "summary": "tts ok",
    "spoken_text": "场景识别完成，等待任务指令",
    "tts_source": "arm_speech_service",
    "tts_backend": "piper",
    "tts_voice": "default"
  }
}
```

## 6. 错误码建议

| 错误码 | 含义 | 是否建议重试 |
|---|---|---|
| `OK` | 成功 | 否 |
| `WAKEUP_TIMEOUT` | 唤醒超时 | 是 |
| `ASR_TIMEOUT` | 未采集到有效语音 | 是 |
| `ASR_ENGINE_UNAVAILABLE` | ASR 模型或依赖不可用 | 否 |
| `AUDIO_DEVICE_UNAVAILABLE` | 麦克风或声卡不可用 | 否 |
| `TTS_EMPTY_TEXT` | 播报文本为空 | 否 |
| `TTS_CANCELLED` | 播报被取消 | 视取消来源 |
| `TTS_ENGINE_UNAVAILABLE` | TTS 模型或依赖不可用 | 否 |
| `SPEECH_SERVICE_UNAVAILABLE` | ARM 服务不可达 | 是 |

## 7. 与 `SpeechAdapter` 的映射

- `asr.final.text` 映射为 `result_digest.instruction`。
- 唤醒来源映射为 `result_digest.wakeup_source`。
- 录音时长映射为 `result_digest.record_seconds`。
- 播报文本映射为 `result_digest.spoken_text`。
- 取消播报映射为 `error_code=TTS_CANCELLED` 或 `result_digest.interrupted=true`。
- `arm_fake` 远端服务不可用时，PC 端可回退到本地 real/text override 模式。
- `arm_real` 远端服务不可用时，仅允许回退到 text override：若 `--speech-asr-text/--speech-asr-text-file` 存在则以 `input_mode=text_override` 成功返回，并在 digest 写入 `speech_fallback_from=arm_speech_service/fallback_error_code/fallback_reason`；若无文本兜底则返回 `SPEECH_SERVICE_UNAVAILABLE`，不启动 PC 本地 real 麦克风链路。

## 8. 验收口径

本草案完成后只做文档验收：

- 协议字段能完整映射现有 `SpeechAdapter` ASR/TTS digest。
- 明确 ARM 服务不承接任务调度或硬件控制。
- 明确服务不可用时可回退 PC 端 speech 实现。
- 未执行真实 ARM 服务验证，因此不追加 `state/experiment_runs.jsonl`。

M28-1 代码验收补充：

- `arm_fake` 覆盖 ASR/TTS 成功、失败、取消与 `SPEECH_SERVICE_UNAVAILABLE` 回退映射。
- `arm_fake` 不生成 TaskJSON、不控制机器人、不写主证据文件。
- 本验收仍未执行真实 ARM 服务验证。

M28-3 代码验收补充：

- `arm_real` 覆盖 HTTP health、ASR、TTS、cancel 与 WS 事件监听入口。
- `arm_real` 服务不可用时只回退 text override，不自动启用 PC 本地 real provider。
- `tools/arm_speech_service/` 提供最小 mock 服务骨架，可用于 PC 本机联调和后续 ARM 端替换 provider。
- 本验收不宣称真实 ARM 板端、real VM 或 real_robot 已通过。

M28-4 代码验收补充：

- `tools/arm_speech_service` 支持 `--provider real`，服务端复用 `aubo_app.adapters.speech` 现有真实语音实现。
- `--provider real` 对外仍返回 `asr_source/tts_source=arm_speech_service`，不泄漏第二套 ASR/TTS digest 口径。
- `--provider mock` 保留为 PC 本机通讯验证入口。
- 自动化测试只验证 provider 选择、协议映射和 CLI/Qt 参数不漂移；真实 ARM 音频硬件通过需另行手工补证。
