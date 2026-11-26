# 生态学晨报 · Ecology Morning Brief

用 Codex 生成可追溯的中文文献晨报。默认检索最近 7 天的生态学、全球变化、生命科学前沿和跨学科方法论文，独立巡检 28 本期刊。每期目标 30–50 篇，其中约 8 篇重点解读，优先正式发表的论文。晨报标注证据来自摘要还是标题与元数据，并记录去重、检索窗口和来源覆盖情况。

## 在 Codex 中运行

1. 克隆或下载仓库，在 Codex 中打开解压后的仓库目录。Codex 会发现 [仓库自带的技能](.agents/skills/ecology-morning-brief/SKILL.md)。
2. 准备 Python 3.11 或更高版本，确保运行环境可访问 Crossref 和 Europe PMC。默认检索使用 Python 标准库，核心文献源可从公共接口访问。
3. 在 Codex 中发起请求，例如：

   > 使用 $ecology-morning-brief 生成今天的中文生态学晨报，重点关注全球变化、种群动态和可迁移的统计方法。

   > 使用 $ecology-morning-brief 深入检索极端高温如何影响变温动物种群的存活和繁殖，按机制组织证据。

每日晨报写入 `outputs/ecology-morning-brief/`。在同一个项目中运行可沿用历史，减少重复推荐。若需要定时接收晨报，在自己的 Codex 环境中创建自动化，并选择这个项目目录。

## 自定义文献范围

- 单次调整时，在提示词中指定研究主题、期刊或时间窗口。
- 长期调整时，按[配置说明](.agents/skills/ecology-morning-brief/references/configuration.md)复制一份本地配置，例如 `config/interests.local.yaml`，并在调用时指定该文件。
- 默认仅依据启动配置和当前请求进行检索。如需从自己的聊天或项目文档更新研究兴趣，先启用上下文刷新，再参考[空白来源配置](.agents/skills/ecology-morning-brief/config/ecology-context-sources.example.json)和[设置步骤](.agents/skills/ecology-morning-brief/references/context-refresh-cli.md)。该功能取决于运行环境可用的访问工具。

[查看命令行检索、结果核对和去重流程](.agents/skills/ecology-morning-brief/references/retrieval-v2.md)。
