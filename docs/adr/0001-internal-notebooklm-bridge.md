# ADR-0001：OA阅答内置 NotebookLM 桥接器

- 状态：已实现首个可运行版本
- 日期：2026-08-11
- 参考：本仓库 NotebookLM 桥接模块与下述架构决策。

## 决策

OA阅答不再要求用户执行 `uv tool install notebooklm-mcp-cli`。NotebookLM 连接改为随主程序运行的 Python 内部模块，OA 业务只依赖 `NotebookLMGateway`，不直接了解 RPC ID、Cookie、CSRF、上传协议或流式响应格式。

## 模块边界

```text
OA 案件 / OA 轮次 / 争点分析
             |
             v
      NotebookLMGateway
       |      |       |
       v      v       v
    Auth    Client  Transport
     |                |
 隔离浏览器       batchexecute / upload
```

- `gateway.py`：OA 工作流可见的唯一接口；创建笔记本、添加来源、配置、提问和状态。
- `auth.py`：本机 Profile、Cookie 与网页令牌；不会把秘密返回给 UI 或日志。
- `cdp.py`：只实现登录所需的最小 Chrome DevTools Protocol，无需 Playwright、Selenium 或额外浏览器。
- `client.py`：Notebook、来源、聊天与引文等操作及领域中立的数据解析。
- `transport.py`：NotebookLM 私有网页协议、XSSI/chunk 解包、上传和网络错误翻译。

## 用户体验

1. 点击“连接 NotebookLM”后 HTTP 请求立即返回。
2. UI 连续显示“打开浏览器 → 等待登录 → 保存本机会话 → 已连接”。
3. 第一次使用在 OA阅答自己的 Chrome/Edge Profile 登录；以后复用。
4. 已使用旧 CLI 的测试用户会一次性迁移旧工具导出的 `cookies.json` 和 `metadata.json`，无需重新登录；旧工具本身不会被调用。
5. PDF、DOCX、OCR Markdown 仍按“每份材料一个来源”上传，不同时提交原件和文字副本。

## 安全与失败方式

- 密码始终只提交给 Google 登录页。
- Cookie 只保存在 `%LOCALAPPDATA%\OA阅答\notebooklm-native`，不会进入案件自定义存档或前端响应。
- 日志、错误和审计只记录操作类型、Notebook ID 与时间，不记录 Cookie、CSRF 或完整请求体。
- 私有网页协议变化时明确返回“网页协议可能已更新”；不会静默改变来源集合，也不会换账号重试。
- RPC ID 可以通过 `OA_NLM_RPC_*` 环境变量热修正，避免为单个 ID 变化重写 OA 业务。

## 兼容性与边界

NotebookLM 没有公开 API，网页协议存在变更风险。当前实现以 2026-08-11 的网页结构为兼容基线，覆盖：

- 本机登录保持和一次迁移；
- 列出/创建 Notebook；
- 原生文件注册、可恢复上传和处理等待；
- 来源清单；
- Chat 自定义提示词；
- 流式问答、conversation ID、引文来源与引文文本。

当前登录仅暴露一个活动 Profile。内部存储已按 Profile 分目录，后续可在不改变 OA 业务接口的情况下增加账号切换。

## 验证

- `tests/test_notebooklm_native.py` 使用离线响应样本验证 RPC 解包、Notebook 解析、来源范围、回答、conversation ID、引文和秘密不外泄。
- 主服务 `/api/notebook/status` 已验证返回 `provider: oa-yueda-native` 与 `setup.nativeEmbedded: true`。
- 受当前执行环境网络策略限制，未在自动测试中向 NotebookLM 发起真实请求；需要在用户正常网络下完成一轮只读 Notebook 列表和测试 Notebook 上传/提问验收。
