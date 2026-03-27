# ZeroClaw 通信范式深度分析 -- TV/移动端部署专题

> ⚠️ **重要澄清**: ZeroClaw + A2A **不是一个插件关系**，而是两种不同的 Agent 通信范式。详见文档末尾的"协议关系说明"章节。

## 1. ZeroClaw vs 标准 A2A 协议对比

| 维度 | 标准 A2A | ZeroClaw | 核心差异 |
|------|---------|----------|---------|
| **Agent 注册** | `/.well-known/agent.json` 端点发布 Agent Card | TOML 配置文件 + 静态配置 | ZeroClaw 无需网络发现，配置即注册 |
| **Agent 发现** | 动态 HTTP 获取 Agent Card | 编译时/启动时配置绑定 | ZeroClaw 不支持运行时动态发现 |
| **元数据格式** | JSON Agent Card (name, capabilities, securitySchemes) | TOML Agent Manifest | 格式不同，语义相似 |
| **消息格式** | Message Parts (text/file/data) + Artifact | Channel Message (Rust struct) | ZeroClaw 更底层，序列化更轻量 |
| **通信方式** | JSON-RPC over HTTPS / gRPC | Channel 抽象（多种实现） | ZeroClaw 支持多种 Channel 类型 |
| **认证机制** | OAuth 2.0 / JWT / API Key | DM Pairing + Bearer Token | ZeroClaw 更简化，适合边缘场景 |
| **任务生命周期** | submitted > working > completed/failed/canceled | Channel 消息流 + 状态机 | A2A 有标准化任务状态模型 |
| **流式支持** | SSE + Push 通知 | WebSocket + HTTP/2 Stream | ZeroClaw 更倾向 WebSocket 全双工 |

---

## 2. ZeroClaw vs OpenClaw 通信架构对比

### 2.1 架构模型差异

```
┌─────────────────────────────────────────────────────────────────┐
│                      OpenClaw (Gateway-Centric)                 │
│  ┌─────────────┐                                                │
│  │   Gateway   │<-- 所有通信必须经过网关                         │
│  │   Server    │    - 集中路由                                   │
│  └──────┬──────┘    - 权限控制                                   │
│         │           - 会话管理                                   │
│    ┌────v────┐                                                  │
│    │Lane Queue│<-- 确定性串行执行                                │
│    └────┬────┘                                                  │
│         │                                                       │
│    ┌────v────┐                                                  │
│    │ Agent   │                                                  │
│    └─────────┘                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    ZeroClaw (Channel-Based)                     │
│  ┌──────────────────────────────────────────┐                   │
│  │           ZeroClaw Core Engine            │                   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │                   │
│  │  │  Core    │ │  HTTP/2  │ │WebSocket │  │<-- 多种 Channel   │
│  │  │ Channel  │ │ Channel  │ │ Channel  │  │    点对点直连     │
│  │  └──────────┘ └──────────┘ └──────────┘  │                   │
│  └──────────────────────────────────────────┘                   │
│         │                                                       │
│    ┌────v────┐     ┌─────────┐                                  │
│    │ Agent A │<--->│ Agent B │<-- Agent 间可直接通信             │
│    └─────────┘     └─────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 关键机制对比

| 特性 | OpenClaw | ZeroClaw |
|------|----------|----------|
| **核心通信** | Lane Queue（确定性串行） | Channel（灵活异步） |
| **消息路由** | Gateway 集中路由 | 点对点直连或经由 Channel |
| **传输协议** | HTTP/REST, WebSocket | HTTP/2, WebSocket, gRPC |
| **消息保证** | At-least-once | At-least-once（可配置） |
| **流式支持** | SSE | WebSocket, HTTP/2 Stream |
| **跨服务通信** | 通过 Gateway | 原生支持多种 Channel |
| **配置格式** | YAML (agents.yaml) | TOML (agent.toml) |
| **内存占用** | 200 MB+ | < 5 MB |
| **启动时间** | 1-5 秒 | < 10 ms |

### 2.3 通信方式对比

OpenClaw 消息必须通过 Gateway：

```python
# OpenClaw: 必须通过 Gateway 路由
sessions_send(
    session_key="agent:coder:local:task-001",  # 需要会话键
    message="在上一版本基础上增加错误处理"
)
```

ZeroClaw 支持点对点直连：

```rust
// ZeroClaw: 点对点消息发送，无需网关
let channel = Channel::http("http://agent-b:8080");
let message = Message::new()
    .with_sender("agent-a")
    .with_recipient("agent-b")
    .with_payload(json!({
        "task": "analyze_data",
        "data": dataset
    }));

channel.send(message).await?;
```

---

## 3. ZeroClaw 核心通信特性

### 3.1 Channel 类型

| Channel | 协议 | 延迟 | 适用场景 |
|---------|------|------|---------|
| **Core Channel** | 内存通道 | < 1us | 单机多 Agent，零拷贝 |
| **HTTP/2 Channel** | HTTP/2 | ~10ms | 跨服务通信，标准 REST API |
| **WebSocket Channel** | WebSocket | ~5ms | 实时流式，双向推送 |
| **gRPC Channel** | gRPC | ~5ms | 内部微服务，高性能二进制 |

### 3.2 外部渠道接入

ZeroClaw 支持 19+ 种外部渠道：

- **即时通讯**: WhatsApp, Telegram, Signal, iMessage, Discord, Slack
- **企业协作**: DingTalk, Lark, Mattermost, Nextcloud Talk
- **社交平台**: Bluesky, Nostr, Reddit, LinkedIn, Twitter
- **中国生态**: QQ, WeChat Work
- **通用协议**: MQTT, IRC, Webhook, Matrix

### 3.3 消息路由机制

通过 `bindings` 配置将特定渠道/会话路由到特定 Agent：

```toml
[channels.telegram]
enabled = true

[channels.telegram.bindings]
# 不同的 Topic 绑定不同 Agent
"topic_123" = { agent = "planner" }
"topic_456" = { agent = "coder" }
```

### 3.4 安全机制

- **DM Pairing**: 未知发送方需通过配对码认证
- **Bearer Token**: Gateway API 认证
- **消息白名单**: 默认拒绝所有入站，需配置 `allow_from`
- **沙箱执行**: 支持 Landlock, Firejail, Bubblewrap, Docker
- **Rust 内存安全**: 编译时所有权检查，零 CVE

---

## 4. TV/移动端部署注意事项

### 4.1 设备资源约束

| 设备类型 | 内存限制 | CPU | 存储 | 网络特点 |
|---------|---------|-----|------|---------|
| **Apple TV 4K** | ~3-4 GB | A15/A17 Pro | 64-128 GB | 稳定 WiFi/以太网 |
| **Android TV** | 2-4 GB | 中端 SoC | 16-64 GB | WiFi 不稳定 |
| **iOS 设备** | 4-8 GB | A 系列芯片 | 128+ GB | 移动网络+WiFi |
| **Android 手机** | 4-12 GB | 高/中端 SoC | 64-256 GB | 移动网络为主 |

### 4.2 ZeroClaw 在 TV/移动端的优势

- 内存占用 < 5 MB（OpenClaw 需要 > 200 MB）
- 启动时间 < 10 ms（用户体验友好）
- 静态链接零依赖（无运行时安装需求）
- 交叉编译支持（arm64, aarch64）
- 支持 MQTT/WebSocket（适合移动网络）
- 本地优先架构（离线可用）

### 4.3 平台兼容性问题

| 平台 | 问题 | 解决方案 |
|------|------|---------|
| **tvOS** | 不支持 JIT，限制后台进程 | 使用 ZeroClaw 静态编译 + App Extension |
| **iOS** | 后台执行限制（约 10 分钟） | 采用 Short/Long Tasks 分离策略 |
| **Android** | Doze 模式限制网络访问 | 使用 WorkManager + FCM 推送唤醒 |
| **Android TV** | 内存更紧张（通常 2-3 GB） | 启用 ZeroClaw 的 SQLite 轻量模式 |

#### tvOS 特殊限制

1. **无后台常驻进程**: tvOS 不允许应用在后台持续运行，Agent 必须在前台活跃时工作
2. **无 JIT 编译**: 对某些动态代码生成策略有限制，ZeroClaw 的静态编译恰好规避
3. **存储限制**: tvOS App 有存储配额，本地 RAG 索引大小需控制
4. **遥控器交互**: UI 设计需适配 Siri Remote，Agent 交互以语音为主，文本输入不便
5. **HomeKit 集成**: 考虑是否需要与 HomeKit 联动，涉及额外的设备发现协议

#### 手机端特殊限制

1. **电池敏感**: WebSocket 长连接消耗电量，需要心跳优化
2. **网络切换**: WiFi/蜂窝网络切换时连接中断，需自动重连机制
3. **内存压力**: 系统可能随时杀死后台进程，状态需持久化
4. **隐私合规**: GDPR/个保法要求，Agent 处理的数据需注意存储位置

### 4.4 网络通信适配

#### 推荐配置

```toml
# TV/移动端推荐配置
[agent.channels]
default = "websocket"              # WebSocket 保持长连接

[agent.channels.websocket]
ping_interval = 30                 # 心跳间隔 (秒)
reconnect_timeout = 5000           # 重连超时 (ms)
offline_queue_size = 100           # 离线消息队列大小

[agent.memory]
type = "sqlite"                    # 本地持久化
path = "./data/memory.db"          # 应用沙盒路径
cache_size = "10MB"                # 缓存大小限制
```

#### 网络策略

| 场景 | 协议选择 | 原因 |
|------|---------|------|
| **TV 实时对话** | WebSocket | 全双工，实时推送流式回答 |
| **手机前台交互** | WebSocket | 低延迟实时通信 |
| **手机后台任务** | HTTP/2 | 按需连接，省电 |
| **低功耗设备** | MQTT | 极低开销，适合 IoT 联动 |
| **弱网环境** | HTTP/2 + 重试 | 短连接容错性好 |

### 4.5 多Agent协作适配

**核心问题**: TV/移动端不适合运行多个重量级 Agent

**推荐方案**: Edge-Cloud 混合架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Edge-Cloud 混合架构                       │
│                                                              │
│  ┌──────────────────────┐       ┌──────────────────────────┐│
│  │   TV/移动端 (Edge)    │       │        云端 (Cloud)       ││
│  │                      │       │                          ││
│  │  ┌────────────────┐  │       │  ┌────────────────────┐  ││
│  │  │   ZeroClaw     │  │HTTP/2 │  │ Orchestrator Agent │  ││
│  │  │  (轻量 Agent)  │<-+-------+->│                    │  ││
│  │  │  - UI 交互     │  │WS/SSE │  │ ┌────────────────┐ │  ││
│  │  │  - 本地 RAG    │  │       │  │ │ Coder Agent    │ │  ││
│  │  │  - 快速响应    │  │       │  │ ├────────────────┤ │  ││
│  │  │  - 离线缓存    │  │       │  │ │ Writer Agent   │ │  ││
│  │  └────────────────┘  │       │  │ ├────────────────┤ │  ││
│  │                      │       │  │ │ Analyzer Agent │ │  ││
│  │  内存: < 50 MB       │       │  │ └────────────────┘ │  ││
│  │  启动: < 100 ms      │       │  └────────────────────┘  ││
│  └──────────────────────┘       │  内存: 无限制             ││
│                                 └──────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

**边缘端（TV/手机）职责**:
- 用户交互与 UI 渲染
- 本地小型 RAG 索引（常用知识）
- 简单查询的本地响应
- 离线状态下的基础功能
- 会话上下文缓存

**云端职责**:
- 复杂多 Agent 编排
- 大规模 RAG 索引检索
- LLM 推理（大模型）
- 跨 Agent 协作与任务分解
- 持久化存储与同步

### 4.6 安全机制适配

| 安全需求 | TV 场景 | 手机场景 | ZeroClaw 实现 |
|---------|---------|---------|--------------|
| **设备认证** | 家庭网络，风险较低 | 移动网络，风险较高 | DM Pairing |
| **数据加密** | 可选择性加密 | 必须端到端加密 | TLS 1.3 |
| **沙箱隔离** | 系统级沙箱 | 系统级沙箱 | Rust 内存安全 |
| **敏感数据** | 本地不存储敏感信息 | KeyChain/Keystore | 需自行对接平台 API |
| **通信认证** | Bearer Token 即可 | OAuth2 + 设备绑定 | DM Pairing + Token |

**注意事项**:
- ZeroClaw 的 DM Pairing 认证较简单，生产环境中手机端建议额外封装 OAuth2 层
- TV 端在家庭网络中相对安全，但需防止局域网内其他设备伪造请求
- 敏感数据（API Key、用户数据）应使用平台原生安全存储（iOS Keychain / Android Keystore）

---

## 5. 与标准 A2A 集成的挑战与方案

### 5.1 核心挑战

| 挑战 | 描述 | 影响 |
|------|------|------|
| **发现机制不兼容** | A2A 需要 HTTP 动态发现 Agent Card；ZeroClaw 静态配置 | 无法接入 A2A 生态的动态 Agent 注册中心 |
| **消息格式差异** | A2A 使用 JSON-RPC 2.0 Message Parts；ZeroClaw 使用 Rust struct | 需要序列化/反序列化适配层 |
| **认证体系差异** | A2A 推荐 OAuth2；ZeroClaw 使用 DM Pairing | 跨系统认证需要桥接 |
| **任务模型差异** | A2A 有标准任务生命周期（submitted/working/completed）；ZeroClaw 用 Channel 消息流 | 状态映射需要额外逻辑 |

### 5.2 集成方案

#### 方案一：ZeroClaw + A2A 适配层（推荐）

```toml
# 使用社区 crate: zeroclaw-a2a
[a2a]
enabled = true
endpoint = "/.well-known/agent-card.json"
methods = ["message/send", "tasks/get", "tasks/cancel"]
auth = "bearer"
```

适用场景：云端 Agent 需要与标准 A2A 生态互通

#### 方案二：NullClaw 替代方案

NullClaw（Zig 实现）原生支持 A2A v0.3.0，比 ZeroClaw 更轻量：

| 特性 | ZeroClaw | NullClaw |
|------|----------|----------|
| **语言** | Rust | Zig |
| **内存** | < 5 MB | ~1 MB |
| **二进制大小** | 3-5 MB | 678 KB |
| **A2A 支持** | 社区 crate | 原生支持 v0.3.0 |
| **启动时间** | < 10 ms | < 8 ms |

适用场景：如果 A2A 兼容性是硬需求，且设备资源极度受限

#### 方案三：云端桥接

边缘端运行 ZeroClaw（不需要 A2A），云端运行 A2A 兼容的 Orchestrator，由云端负责与外部 A2A Agent 交互。

---

## 6. AI tvOS POC 推荐架构

### 6.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI tvOS POC 推荐架构                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Apple TV (tvOS)                       │   │
│  │                                                          │   │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │   │
│  │  │   SwiftUI   │    │  ZeroClaw   │    │   SQLite    │  │   │
│  │  │     UI      │<-->│   Engine    │<-->│   Memory    │  │   │
│  │  │  (前端)     │    │ (本地Agent) │    │  (本地存储)  │  │   │
│  │  └─────────────┘    └──────┬──────┘    └─────────────┘  │   │
│  │                            |                             │   │
│  │                            | WebSocket / HTTP/2          │   │
│  │                            v                             │   │
│  └────────────────────────────┼─────────────────────────────┘   │
│                               |                                 │
│  ┌────────────────────────────v─────────────────────────────┐   │
│  │                    云端服务层                              │   │
│  │                                                          │   │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │   │
│  │  │ Orchestrator│    │  RAG Index  │    │    LLM      │  │   │
│  │  │   Agent     │--->│  (向量库)   │--->│   Service   │  │   │
│  │  └─────────────┘    └─────────────┘    └─────────────┘  │   │
│  │        |                                                 │   │
│  │        v                                                 │   │
│  │  ┌─────────────────────────────────────────────────┐     │   │
│  │  │         专业 Agent Pool (A2A 兼容)               │     │   │
│  │  │  Coder | Writer | Analyzer | Translator | ...   │     │   │
│  │  └─────────────────────────────────────────────────┘     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 技术选型建议

| 层级 | 技术选型 | 原因 |
|------|---------|------|
| **TV 前端** | SwiftUI + UIKit | tvOS 原生，支持 Siri Remote |
| **TV 本地 Agent** | ZeroClaw (Rust FFI) | 轻量，快速启动，内存安全 |
| **TV 本地存储** | SQLite (via ZeroClaw) | 轻量持久化，离线可用 |
| **TV-云端通信** | WebSocket + HTTP/2 | 实时流式 + 按需请求 |
| **云端编排** | ZeroClaw / OpenClaw | 根据复杂度选择 |
| **云端 A2A** | zeroclaw-a2a 适配层 | 与标准 A2A 生态互通 |
| **LLM 服务** | 云端 API（按需选择） | 避免在 TV 端运行大模型 |

### 6.3 核心设计原则

1. **边缘轻量，云端重量**: TV 端只运行轻量 Agent，复杂推理交给云端
2. **离线优先**: 基础功能（本地 RAG、缓存对话）在离线时也可用
3. **流式体验**: 使用 WebSocket 实现 LLM 流式输出，用户无需等待完整响应
4. **优雅降级**: 云端不可达时，TV 端 Agent 应能提供有限但可用的服务
5. **安全分层**: 边缘端 DM Pairing + TLS，云端 OAuth2 + A2A 标准认证

---

## 7. 总结

| 考量维度 | 建议 |
|---------|------|
| **框架选择** | ZeroClaw 用于边缘端；如需 A2A 原生支持可评估 NullClaw |
| **通信协议** | WebSocket 为主（实时流式），HTTP/2 为辅（按需请求），MQTT 用于低功耗 IoT 联动 |
| **多Agent模式** | TV 端运行单 Agent（本地交互），复杂多 Agent 任务委派云端 |
| **离线能力** | SQLite 本地记忆 + 小型向量索引，支持离线基础功能 |
| **安全策略** | DM Pairing + TLS 1.3，敏感数据用 iOS Keychain / Android Keystore |
| **A2A 兼容** | 云端 Agent 使用 A2A 标准协议，边缘端通过适配层或云端桥接 |
| **tvOS 关注** | 无后台常驻、无 JIT、存储配额、遥控器交互适配、语音优先 |
| **手机关注** | 电池优化、网络切换重连、内存压力应对、隐私合规 |

---
## 8. 协议关系说明（重要澄清）

### 8.1 ZeroClaw + A2A ≠ 插件关系

**常见误解纠正**：
很多开发者误以为 ZeroClaw 是 A2A 协议的一个实现或插件，这是不正确的。

### 8.2 两者本质区别

| 维度 | ZeroClaw | A2A Protocol |
|------|----------|-------------|
| **性质** | 轻量级 Agent 框架 | 标准化互操作协议 |
| **主导方** | 社区开源项目 | Google 主导 |
| **实现方式** | Rust 编写的完整框架 | 协议规范（JSON-RPC） |
| **部署形态** | 单体可执行文件 | 任意语言实现的协议 |

### 8.3 关系类比

```
错误理解:
ZeroClaw ───是───> A2A Plugin
    ↓              ↗
  Agent Framework 

正确理解:
ZeroClaw        A2A Protocol
    │               │
    ▼               ▼
Agent Framework   Protocol Spec
    │               │
    └────┬────┬────┘
         │    │
    可集成  可桥接
```

### 8.4 集成方式

如果需要两者协同工作，有三种可行方案：

1. **适配层方案**: 使用 `zeroclaw-a2a` crate 做协议转换
2. **替代方案**: 使用原生支持 A2A 的 NullClaw（Zig 实现）
3. **桥接方案**: 边缘用 ZeroClaw，云端用 A2A，中间做协议桥接

详细集成指南见: [`docs/zeroclaw_vs_a2a_integration_guide.md`](./zeroclaw_vs_a2a_integration_guide.md)

### 8.5 选型建议

| 场景 | 推荐方案 | 原因 |
|------|---------|------|
| **TV/移动端** | ZeroClaw | 轻量、快速启动、低内存 |
| **企业级协作** | A2A | 标准化、生态完善 |
| **混合架构** | ZeroClaw + 桥接 | 边缘轻量 + 云端标准 |

---

## 9. 参考资料

### 本项目相关文档

- [`docs/zeroclaw_vs_a2a_integration_guide.md`](./zeroclaw_vs_a2a_integration_guide.md) - ZeroClaw 与 A2A 协议详细集成指南
- [`docs/agent_protocol_selection_matrix.md`](./agent_protocol_selection_matrix.md) - 多协议选型决策矩阵
- [`agent_communication_survey.md`](./agent_communication_survey.md) - Agent 通信协议调研报告

### ZeroClaw & NullClaw

- [ZeroClaw GitHub 官方仓库](https://github.com/zeroclaw-labs/zeroclaw)
- [ZeroClaw 架构文档](https://github.com/zeroclaw-labs/zeroclaw/tree/master/docs/architecture)
- [ZeroClaw 快速入门指南](https://github.com/zeroclaw-labs/zeroclaw/blob/master/docs/setup-guides/one-click-bootstrap.md)
- [ZeroClaw CLI 命令参考](https://github.com/zeroclaw-labs/zeroclaw/blob/master/docs/reference/cli/commands-reference.md)
- [ZeroClaw: A Minimal Rust-Based AI Agent Framework](https://dev.to/lightningdev123/zeroclaw-a-minimal-rust-based-ai-agent-framework-for-self-hosted-systems-5593)
- [NullClaw GitHub 仓库](https://github.com/nullclaw/nullclaw) - 原生支持 A2A v0.3.0 的 Zig 实现

### A2A Protocol

- [A2A Protocol 官方规范](https://a2a-protocol.org/latest/specification/)
- [Announcing the Agent2Agent Protocol (A2A) - Google](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)
- [A2A GitHub Repository](https://github.com/a2aproject/A2A)
- [Google Open-Sources Agent2Agent Protocol - InfoQ](https://www.infoq.com/news/2025/04/google-agentic-a2a/)

### OpenClaw

- [OpenClaw Multi-Agent: Subagents, Agent Teams & Orchestration](https://www.meta-intelligence.tech/en/insight-openclaw-multi-agent)
- [OpenClaw Architecture: Build Production AI Agents](https://pub.towardsai.net/openclaw-architecture-deep-dive-building-production-ready-ai-agents-from-scratch-e693c1002ae8)
- [Multi-agent orchestration patterns - OpenClaw Issues](https://github.com/openclaw/openclaw/issues/43034)

### 协议对比分析

- [A2A vs MCP vs ACP - Medium](https://dinmaybrahma.medium.com/the-great-ai-agent-protocol-battle-a2a-vs-mcp-vs-acp-8c232811db30)
- [MCP vs A2A vs ANP vs ACP: Choosing the Right AI Agent Protocol](https://insights.firstaimovers.com/mcp-vs-a2a-vs-anp-vs-acp-choosing-the-right-ai-agent-protocol-70da0b6e10a0)
- [Comparison of Agent Protocols MCP, ACP and A2A](https://heidloff.net/article/mcp-acp-a2a-agent-protocols/)

### Apple tvOS 开发

- [Apple Developer - Machine Learning & AI](https://developer.apple.com/machine-learning/)
- [tvOS Developer Documentation](https://developer.apple.com/tvos/)
- [App Extensions - Apple Developer](https://developer.apple.com/app-extensions/)
- [The State of Agentic iOS Engineering in 2026](https://dimillian.medium.com/the-state-of-agentic-ios-engineering-in-2026-c5f0cbaa7b34)

### 安全与认证

- [Security in Agentic Communication - Medium](https://medium.com/@adnanmasood/security-in-agentic-communication-threats-controls-standards-and-implementation-patterns-for-bf1eadc94e95)
- [Best Practices for Agent-to-Agent Authentication](https://prefactor.tech/blog/best-practices-for-agent-to-agent-authentication)

### 架构模式

- [Agent Orchestration Patterns: Swarm vs Mesh vs Hierarchical](https://gurusup.com/blog/agent-orchestration-patterns)
- [Multi-Agent Architectures - Swarms Framework](https://docs.swarms.world/en/latest/swarms/concept/swarm_architectures/)
- [The Ultimate Guide to AI Agent Architectures in 2025](https://dev.to/sohail-akbar/the-ultimate-guide-to-ai-agent-architectures-in-2025-2j1c)

---

**文档创建时间**: 2026年3月
**基于**: agent_communication_survey.md 调研报告 + ZeroClaw/NullClaw 官方文档
**适用场景**: AI tvOS POC (基于 ZeroClaw)
