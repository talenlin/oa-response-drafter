# 第三方组件说明

OA阅答 v1.0.1 的 NotebookLM 桥接器已经内置，标准安装包不再为此附带 `uv.exe` 或下载 Python 包。
- **NotebookLM 连接说明**：OA阅答 v1.0.1 使用自有的本机桥接模块，不再下载或运行 `notebooklm-mcp-cli`。为兼容既有测试用户，可一次性迁移该工具此前导出的本机 Cookie 与会话元数据；迁移内容不会写入日志或上传到 OA阅答 服务器。

OA阅答不会随安装包预装 NotebookLM 的登录凭据，也不会读取用户日常浏览器 Profile 或 Google 密码；登录资料仅保存在 OA阅答 自己的本机隔离 Profile 中。

当使用轻量版构建时，OA阅答还会随包包含 **OfficeOxide 0.1.15**，用于在未安装 Microsoft Word 的电脑上将旧 `.doc` 本机转换为 `.docx`。OfficeOxide 采用 **MIT OR Apache-2.0** 双重许可；其源码、许可证文本与版权信息见 <https://github.com/yfedoseev/office_oxide>，随包许可证位于 `app/runtime/python/Lib/site-packages/office_oxide-0.1.15.dist-info/licenses`。构建脚本会固定并校验 Windows wheel 的 SHA-256，避免下载替换。
