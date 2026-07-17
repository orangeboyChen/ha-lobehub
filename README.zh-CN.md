# LobeHub Home Assistant Integration

[English](README.md) | [简体中文](README.zh-CN.md)

用于 LobeHub 的 Home Assistant 自定义集成。每个已配置的 LobeHub 智能体都会作为独立的 Home Assistant 对话实体提供服务。

## 使用要求

- Home Assistant 2026.7 或更高版本
- 可从 Home Assistant 访问的 LobeHub 服务器
- 有权列出并调用目标智能体的 LobeHub API Key

## 通过 HACS 安装

1. 在 HACS 中打开**集成**，将本仓库作为自定义仓库添加，分类选择**集成**。
2. 在 HACS 中下载 **LobeHub** 并重启 Home Assistant。
3. 打开**设置 > 设备与服务 > 添加集成**，选择 **LobeHub**，填写服务器根地址和 API Key。
4. 选择一个或多个智能体。Home Assistant 会为每个智能体创建一个配置条目和一个对话实体。

手动安装时，将 `custom_components/lobehub` 复制到 Home Assistant 配置目录中的相同路径，然后重启 Home Assistant。

## 配置与行为

先在 LobeHub 服务器中创建 API Key，再通过配置流程填写。服务器地址应为根地址，例如 `https://lobehub.example`，不要附加 `/api/v1`。

每个配置条目独立保存智能体绑定和当前 LobeHub 话题。可在条目选项中选择新对话复用当前话题或每次创建新话题，并按需覆盖模型、提供商、执行目标与绑定设备。删除条目只会删除 Home Assistant 配置，不会删除 LobeHub 智能体或话题。

配置多个智能体后，请在自动化或服务调用中目标选定对应的 LobeHub 对话实体。未指定目标的调用仅在恰好有一个已加载条目时有效。

## 服务

集成提供以下可目标选定的服务：

- `lobehub.send_message`：向当前话题发送消息。
- `lobehub.new_topic`：创建并切换到新话题。
- `lobehub.switch_topic`：根据 ID 切换到已有话题。
- `lobehub.run_task`：通过当前智能体运行临时任务。
- `lobehub.list_tasks`、`lobehub.get_task`、`lobehub.run_saved_task`：查询和运行已保存任务。
- `lobehub.list_agents`、`lobehub.list_devices`：返回自动化配置所需的 ID。
- `lobehub.update_agent_settings`：更新模型、提供商、执行目标、绑定设备和话题策略。

服务字段和支持的目标选择器可在 Home Assistant 自动化编辑器以及 [`services.yaml`](custom_components/lobehub/services.yaml) 中查看。

## 故障排查

- **无法连接或验证 API Key：** 确认 Home Assistant 可以访问服务器地址，且 API Key 对应当前服务器。
- **API Key 验证成功但没有智能体：** API Key 必须具备列出选定 LobeHub 工作区智能体的权限。
- **服务提示需要目标：** 选择一个 LobeHub 对话实体，尤其是在配置了多个智能体时。
- **已配置智能体消失：** 当 LobeHub 报告远端智能体已不存在时，集成会删除对应的 Home Assistant 条目。

## 开发与发布

安装 [uv](https://docs.astral.sh/uv/) 后，依次运行 `uv sync --group dev --locked`、`uv run python -m compileall -q custom_components tests`、`uv run ruff check custom_components tests` 和 `uv run pytest`。版本号与发布步骤请参见 [RELEASE.md](RELEASE.md)。
