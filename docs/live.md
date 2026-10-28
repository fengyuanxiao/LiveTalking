# 主动讲解与弹幕互动

启动原有 `app.py` 后打开 `http://localhost:8010/live.html`。首页也有“直播控制台”入口。

## 使用步骤

1. 点击“连接数字人”，或刷新并选择已经存在的 WebRTC / RTMP / 虚拟摄像头会话。使用当前配置的数字人、音色和模型。
2. 填写产品名称、类别、资料，保存产品。资料可包含规格、材质、价格、优惠条件、售后和常见问题；可保存多个不同类别的产品。
3. 选择“AI 讲解与问答”或“固定稿循环”。AI 模式使用 `.env` 里已配置的 LLM 密钥；固定稿无需大模型，但需要填写逐行分段的讲解稿。
4. 选择平台。如果要接外部弹幕，填写直播间 ID；仅模拟测试可不填写。
5. 点击“开始 / 应用新设置”。在右侧发模拟弹幕，测试讲解 → 回答 → 继续讲解。
6. 换品时选择或保存新产品，再点击“应用新设置”。旧任务停止，新产品从第一段开始。仅编辑保存产品不会改变正在进行的讲解。

固定稿模式对弹幕统一回应“需要确认”，不会根据资料智能回答。实际问答请使用 AI 模式。AI 内容以资料为依据，但仍需通过试听核对；需要逐字准确的介绍可使用已审核的固定稿。

## 互动节奏与停止

- 一次只向语音队列提交一小段，通过输出侧的播报结束事件推进。合成中的静音不会被当成“讲完”。
- 普通弹幕在当前段落结束后处理；同一文本和事件 ID 在 90 秒内去重。队列最多 30 条，90 秒未回答的弹幕过期。
- 回复间隔至少 12 秒；连续回答最多两次后恢复产品介绍。回答保留最近上下文，并继续原介绍进度。
- 段间停顿为 0.8～1.8 秒。AI 生成与 TTS 请求耗时还会增加停顿，网络不稳定时会更明显。
- 停止、换品、断线会取消调度；旧生成结果和旧任务的新音频不再进入播报。已到达播放器的缓冲内容可能短暂残留。
- 自动讲解期间，原 `/human` 和上传音频入口会提示先停止，避免两个来源同时控制主播；`/interrupt_talk` 同时停止自动讲解。
- EdgeTTS 遇到无音频或网络错误最多重试两次，每次重建音频缓存；连续失败会停止。其他 TTS 插件仍沿用原合成逻辑。
- 当前阶段先生成再合成。默认 90 秒没有播放开始、开始后 300 秒没有结束会停止并报错。可在控制台查看生成、合成、播报、暂停和错误状态。

## 七个平台的接入状态

| 平台 | platform ID | 当前真实弹幕接入 |
| --- | --- | --- |
| 拼多多 | `pinduoduo` | 需要外部平台适配器 |
| 抖音 | `douyin` | 需要外部平台适配器 |
| 小红书 | `xiaohongshu` | 需要外部平台适配器 |
| 快手 | `kuaishou` | 需要外部平台适配器 |
| B站 | `bilibili` | 需要外部平台适配器 |
| 视频号 | `weixin` | 需要外部平台适配器 |
| 淘宝 | `taobao` | 需要外部平台适配器 |

已实现平台和直播间隔离、统一入口、认证、去重和转发工具。选择平台不会自动登录、采集弹幕或开始向平台推流。各平台的评论采集需要对应可用接口或已授权的连接服务，将消息转换为下述格式；仓库未附带七个平台的原生采集器。不同平台可使用不同会话和产品，单个会话每次绑定一个平台与直播间。

直播画面仍通过项目原有 WebRTC、RTMP、虚拟摄像头输出。控制台预览不会自动向上述平台开播；需要通过相应直播工具配置画面和声音来源。

### 统一弹幕入口

服务端 `.env` 设置随机共享密钥后重启：

```dotenv
LIVE_INGEST_TOKEN=替换为自行生成的长随机密钥
```

适配器请求：

```http
POST /api/live/ingest
Authorization: Bearer <LIVE_INGEST_TOKEN>
Content-Type: application/json
```

```json
{
  "platform": "douyin",
  "room_id": "your-room-id",
  "event_id": "unique-comment-id",
  "username": "观众昵称",
  "text": "有哪些规格？"
}
```

按 `platform + room_id` 分发到正在运行的匹配会话，不接受请求指定任意会话。响应 `deliveries` 为空表示没有匹配的正在运行会话；`accepted` 表示入队，`duplicate` 表示合并，`queue_full` 表示队列已满。未配置密钥返回 503，密钥错误返回 401。

现有平台适配器若能输出逐行 JSON，可接 `forward_comments.py`：

```text
{"event_id":"1","username":"观众","text":"有哪些规格？"}
```

```powershell
# 上游适配器输出 JSONL 到 stdin；双方使用同一个 LIVE_INGEST_TOKEN。
python your_platform_adapter.py | python forward_comments.py --platform douyin --room your-room-id
```

`your_platform_adapter.py` 代表你接入平台的程序，不是仓库自带脚本。转发工具不负责登录或采集。中文 JSONL 管道需使用 UTF-8。

## 控制 API

沿用 `{ "code": 0, "msg": "ok", "data": ... }` 格式。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | `/api/live/products` | 获取产品库 / 保存产品 |
| GET | `/api/live/platforms` | 平台 ID 与接入状态 |
| POST | `/api/live/start` | 开始或切换产品 |
| POST | `/api/live/stop` | 停止，body 含 `sessionid` |
| GET | `/api/live/status?sessionid=...` | 讲解进度、阶段、队列及错误 |
| POST | `/api/live/comment` | 本地模拟弹幕，body 含 `sessionid`、`text`、可选 `username` |
| POST | `/api/live/ingest` | 外部适配器认证入口 |

产品保存 body：

```json
{"name":"产品名称","category":"类别","facts":"已核实的产品资料","scripts":["第一段讲解稿","第二段讲解稿"]}
```

更新已有产品时带上返回的 `id`。产品库保存在 `data/live_products.json`，属于本地数据，不提交到 Git。讲解稿每段最多 240 字，最多 100 段；产品资料最多 12000 字。

开始 body：

```json
{"sessionid":"已有会话ID","product_id":"已保存的产品ID","platform":"douyin","room_id":"your-room-id","mode":"llm","style":"自然亲切"}
```

`mode` 可选 `llm` / `script`，`style` 可选“自然亲切”“专业清晰”“轻松活泼”。控制 API 与原项目一样供本机或受保护的控制端使用，外部平台适配器仅调用带密钥的 `/api/live/ingest`。

## 自然感的实际限制

已加入短段口语、上下文衔接、最近内容避重复、变动停顿和互动限频。形象动作来自现有 avatar 素材，声音来自所选 TTS；本功能没有增加实时手势、表情驱动或声音克隆模型。

CPU 推理时可能只有几 FPS，说话会明显卡顿；空闲画面达到 25 FPS 并不代表说话实时。应以日志里说话期间的 `inferfps` 和 `finalfps` 评估，目标均为至少 25。更自然的效果还需要流畅的 GPU 推理、合适的真人素材、音色和稳定的语音服务，无法仅靠提示词保证。
