# 生态学晨报 · Ecology Morning Brief

用 Codex 生成可追溯的中文文献晨报。默认检索最近 7 天的生态学、全球变化、生命科学前沿和跨学科方法论文，独立巡检 28 本期刊。每期目标 30–50 篇，其中约 8 篇重点解读，优先正式发表的论文。晨报标注证据来自摘要还是标题与元数据，并记录去重、检索窗口和来源覆盖情况。

## 在 Codex 中运行

1. 克隆或下载仓库，在 Codex 中打开解压后的仓库目录。Codex 会发现 [仓库自带的技能](.agents/skills/ecology-morning-brief/SKILL.md)。
2. 准备 Python 3.11 或更高版本，确保运行环境可访问 Crossref 和 Europe PMC。默认检索使用 Python 标准库，核心文献源可从公共接口访问。
3. 在 Codex 中发起请求，例如：

   > 使用 $ecology-morning-brief 生成今天的中文生态学晨报，重点关注全球变化、种群动态和可迁移的统计方法。

   > 使用 $ecology-morning-brief 深入检索极端高温如何影响变温动物种群的存活和繁殖，按机制组织证据。

每日晨报写入 `outputs/ecology-morning-brief/`。在同一个项目中运行可沿用历史，减少重复推荐。若需要定时接收晨报，在自己的 Codex 环境中创建自动化，并选择这个项目目录。

## 一键检索与测试

默认检索依赖 Python 3.11 及以上的标准库。配置文件虽沿用 `.yaml` 扩展名，写入时须使用 JSON 兼容格式。下列命令在 macOS 或 Linux 上生成候选、请求审计和覆盖报告；随后可在 Codex 中基于这些记录生成中文晨报。

```bash
./run_brief.sh /path/to/project
python3 -B -m unittest discover -s .agents/skills/ecology-morning-brief/scripts -p '*test*.py'
```

脚本优先使用项目的 `config/interests.local.yaml`。已启用聊天／项目刷新时，先在 Codex 中完成刷新，再通过 `--config` 和 `--profile` 传入生效配置与画像。审计文件与候选文件同目录保存，包含查询参数、分页、重试、阶段计数和配置 SHA-256。

## 设计取舍

Crossref 按 ISSN 提供出版社登记的正式版本线索，Europe PMC 补充生命科学主题与作者摘要，两者共同作为公开主源。OpenAlex 用于弥补特定期刊或方法主题的覆盖缺口，以及深搜中的有界引文扩展。PubMed 用于生命科学补检。按来源设置查询和分页上限，并在审计中记录限制。

28 本种子期刊让 CNS 前沿、生态学和方法论文都有独立入口，同时把日常巡检控制在可审阅范围。增加期刊时看增量论文的质量、与其他渠道的重叠和运行成本。期刊归属用于组织阅读，发表版本与作者证据逐篇核实。

## 按需增加期刊

在 Codex 中直接说：

> 使用 $ecology-morning-brief 生成今天的晨报，本次也巡检 Journal of Experimental Biology。

> 将 Journal of Experimental Biology 加入以后每期的巡检名单。

Codex 会核对期刊全名与 ISSN，将确认的期刊附加到现有巡检清单，并报告添加结果。日刊和周刊会按 ISSN 独立检索这些期刊，文章随后进入筛选与去重。单次请求使用临时配置，长期请求保存到本项目的 `config/interests.local.yaml`。你也可以让 Codex 按研究方向推荐并加入期刊，或直接提供 ISSN。

如果已核对期刊信息，可以从仓库根目录执行：

```bash
SKILL_DIR="$PWD/.agents/skills/ecology-morning-brief"
python3 "$SKILL_DIR/scripts/extend_journal_watch.py" \
  --base "$SKILL_DIR/config/interests.yaml" \
  --journal "Journal of Experimental Biology" 1477-9145 "The Journal of Experimental Biology" \
  --output config/interests.local.yaml
```

已有本地配置时，将 `--base` 指向该文件以保留其中的设置。多本期刊可重复传入 `--journal`。

## 根据聊天更新兴趣

公开版包含从聊天和项目文档提取研究兴趣的功能，默认关闭。要启用它，在本地配置中设置 `context_refresh.enabled` 为 `true`，再按步骤添加自己授权的聊天或项目文件。它会累积有来源依据的主题和检索词，并在晨报中记录变化与读取限制。该功能取决于当前 Codex 环境可用的访问工具。 [空白来源配置](.agents/skills/ecology-morning-brief/config/ecology-context-sources.example.json) · [设置步骤](.agents/skills/ecology-morning-brief/references/context-refresh-cli.md)

## 默认巡检期刊（28 本）

这是每天按期刊独立巡检的名单。关键词检索也会从其他期刊发现相关论文。 [期刊配置](.agents/skills/ecology-morning-brief/config/interests.yaml)

| 组别 | 期刊 | ISSN |
| --- | --- | --- |
| CNS 系列 | Nature | `0028-0836` |
| CNS 系列 | Science | `0036-8075` |
| CNS 系列 | Cell | `0092-8674` |
| CNS 系列 | Nature Ecology & Evolution | `2397-334X` |
| CNS 系列 | Nature Climate Change | `1758-678X` |
| CNS 系列 | Nature Communications | `2041-1723` |
| CNS 系列 | Nature Methods | `1548-7105` |
| CNS 系列 | Nature Biotechnology | `1087-0156` |
| CNS 系列 | Nature Machine Intelligence | `2522-5839` |
| CNS 系列 | Nature Computational Science | `2662-8457` |
| CNS 系列 | Nature Sustainability | `2398-9629` |
| CNS 系列 | Nature Geoscience | `1752-0894` |
| CNS 系列 | Nature Water | `2731-6084` |
| CNS 系列 | Science Advances | `2375-2548` |
| CNS 系列 | Current Biology | `0960-9822` |
| CNS 系列 | Cell Systems | `2405-4712` |
| CNS 系列 | Trends in Ecology & Evolution | `0169-5347` |
| CNS 系列 | Trends in Genetics | `0168-9525` |
| 综合科学 | Proceedings of the National Academy of Sciences | `0027-8424` |
| 综合科学 | PNAS Nexus | `2752-6542` |
| 生态学 | Ecology Letters | `1461-023X` |
| 生态学 | Global Change Biology | `1354-1013` |
| 生态学 | Methods in Ecology and Evolution | `2041-210X` |
| 生态学 | Ecography | `0906-7590` |
| 生态学 | Journal of Animal Ecology | `0021-8790` |
| 生态学 | Functional Ecology | `0269-8463` |
| 生态学 | Global Ecology and Biogeography | `1466-822X` |
| 生态学 | Ecology | `0012-9658` |

[查看命令行检索、结果核对和去重流程](.agents/skills/ecology-morning-brief/references/retrieval-v2.md)。

本仓库代码和文档采用 [MIT 许可证](LICENSE)。参与协作请见 [贡献说明](CONTRIBUTING.md)。
