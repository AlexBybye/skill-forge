# Skill Forge — 优化、评估、创建

[English](README.md) · 中文

**用证据优化已有 Skill，检验何时不需要 Skill，只在真实任务确实需要时创建。**

> **Skill Forge 是面向可复用 AI Agent Skill 的证据驱动系统：优化已有版本、评估是否需要 Skill，并在确有必要时创建小型候选。**

Skill Forge 先解决两个代价最高的问题：已有 Skill 能否针对真实失败得到改进，以及选定任务是否根本不需要 Skill。只有证据支持时才构建候选，并明确报告已证明、已否证和未验证的部分。

它既不是用来快速起草 Skill 的提示词模板，也不是只在成品阶段检查包体的 release gate。它把创作放进一条证据闭环：冻结不迎合候选的验收用例，保留不可变基线，再构建、比较、保留更好的结果，并限制最终声明的边界。

它止于候选交接。安装、发布、提交和发行仍是彼此分离、需要单独授权的动作。

Python 3.10+，只用标准库。无网络请求，无第三方依赖。

`VERSION` 是独立仓库的项目版本真值。本次迁出的基线从新的独立版本线 `0.1.0` 开始；只有经过验证并打上 tag 的 clean commit 才算 release。

## Skill Forge 裁决什么

| 模式 | 它保护什么 | 有证据边界的结果 |
|---|---|---|
| `optimize` | 不可变 baseline 与一个已观察失败 | 选定用例无关键回归且确有改善才采纳，否则 `keep_baseline` |
| `no_skill` | 不构建候选的 baseline 探针 | `no_skill_supported_for_selected_cases` 或 `no_skill_not_supported_for_selected_cases` |
| `create` | 在实现前确认真实需求 | 构建小型候选并交接或拒绝；没有对照就不宣称 gain |

如果已有相邻 Skill 足够，停在上游 `reuse` triage，不要重复创建；`reuse` 不是可执行的 suite 模式。

## 为什么不只用 skill-creator

creator 和 Skill Forge 回答的不是同一个问题：

| | skill-creator | Skill Forge |
|---|---|---|
| 首要问题 | 这个 Skill 应该怎么写？ | 是否真的需要 Skill？这版是否确实更好？ |
| 起点 | 一个创作请求 | 真实任务、已观察失败或重复成本 |
| 评估方式 | 通常审阅生成结果 | 候选出现前冻结用例，再比较原始结果 |
| baseline | 可选 | 优化时必须不可变；no-skill 也是正式基线 |
| 失败终局 | 继续改稿 | `keep_baseline`、`no_skill`、`reject_candidate`、`inconclusive` 都是有效结果 |
| 声明方式 | 描述做了什么 | 分开记录已证明、已否证、未验证、未运行、不可观测 |

两者可以配合：skill-creator 可以承担起草阶段，Skill Forge 决定这份草稿是否值得留下。release gate 从候选已经存在之后开始；Skill Forge 从实现之前开始，在安装和发布之前结束。

它的辨识度不是产出更多文件，而是保住这条因果链：

```text
真实任务 -> 冻结验收标准 -> 隔离构建候选
        -> 可比原始观测 -> 有证据边界的决策
```

## 真机实测：相同 Opus 5，不同引导

本次使用非最新版 Skill Forge 优化一个播放器回归 Skill。基线与候选均由同一个 **Opus 5** 模型生成；主要差异是模型是否经过 Skill Forge 的优化约束。

本轮明确给出的优化目标是**提升执行速度**。准确度并不是额外塞给模型追逐的并列指标；在围绕速度优化的过程中，Skill Forge 的验收与证据流程主动暴露了正确性缺陷，候选随后自行完成修缮。因此，下表中的准确度/稳定性提升是优化过程中发现的伴生收益，不是为了预设结论而定义的成功条件。

真机环境为生产视频站点 + 系统 Chrome 150，执行相同的 29 条播放器用例。两侧各跑 4 轮（1 轮冷启动、3 轮热运行），跳过数均为 0。

| 实测指标 | 基线 | Skill Forge 候选 | 变化 |
|---|---:|---:|---:|
| 热轮墙钟中位数 | 337.5 秒 | 117.4 秒 | **-220.1 秒 / -65.2%** |
| 有效吞吐 | 0.086 条/秒 | 0.247 条/秒 | **2.87 倍** |
| 4 轮累计通过 | 105 / 116 | 107 / 116 | **+2 条 / +1.7 个百分点** |
| 热轮通过数中位数 | 26 / 29 | 27 / 29 | **+1 条 / +3.4 个百分点** |
| 跳过数 | 0 | 0 | 实际执行集合相同 |

本次记录为**执行时间降低 65.2%**、吞吐提升至 2.87 倍、四轮累计通过增加 2 条。两侧调用的都是 Opus 5。

优化同时修正了控件探查过早、不可播终态处理和 seek 判据提前通过的问题。

### 实机运行截图

![实机运行中的目标与模式冻结](assets/readme/goal-and-mode-freeze.png)

*实现前收集真实任务、已观察失败、模式、候选位置和真机测试预算。*

![实机运行中的冻结用例审阅](assets/readme/case-suite-review.png)

*冻结前逐条展示核心、边界、失败和近负例，等待用户确认。*

## 实测案例：多源汇总工作周报 Skill

Skill Forge 还被用于优化一个多源采集并生成工作周报的 Skill。这次主要修改的是 Skill 的**触发、流程和授权契约**，没有修改底层采集、上传或业务集成代码。

| 实测指标 | 修改前 | 修改后 | 变化 |
|---|---:|---:|---:|
| Skill Forge 结构错误 | 1 | 0 | 身份校验通过 |
| 冻结用例整案通过 | 0 / 7 | 2 / 7 | **+28.6 个百分点** |
| 预期字段命中 | 20 / 58（34.5%） | 49 / 58（84.5%） | **+50.0 个百分点** |
| 不匹配字段 | 38 | 9 | **减少 76.3%** |
| `SKILL.md` 行数 | 155 | 131 | **减少 15.5%** |
| `SKILL.md` 字节 | 9,997 | 9,060 | **减少 9.4%** |
| 活跃说明和元数据总量 | 214 行 | 177 行 | **减少 17.3%** |
| UI 元数据字段 | 0 | 3 | 新增名称、简介和默认提示 |
| 明确排除的误触发类型 | 0 | 3 | 文档摘要、团队摘要和通用日历 |
| 底层业务文件变化 | 0 / 20 | 0 / 20 | 20 个文件全部逐字节一致 |

整案采用严格 JSON 比较，只要一个嵌套字段名称不同，整个用例就会失败。本次结果为**整案通过 2/7**、**字段命中 49/58**，剩余 9 个不匹配字段。

在保持 20 个业务代码和配置文件不变的前提下，本轮完成了：

- 将非法的下划线身份改为可发现的连字符身份；
- 将模糊的排期/任务触发收窄到明确的个人排期场景，并排除三类相邻请求；
- 固定环境检查、来源采集、OKR 提取、AI 匹配、人工复核、确定性渲染六段流程；
- 为非法周次、缺少凭据和外部写入状态定义稳定语义；
- 对创建/删除、上传、追踪同步和 Hook 安装实行“展示精确目标 -> 紧邻确认 -> 执行”，目标变化后重新确认；
- 所有命令通过 `SKILL_ROOT` 定位，不再依赖当前目录；
- 将任务类型、子任务约束和环境变量移入按需读取的配置参考；
- 删除历史型 README，补齐名称、简介和默认提示三个 UI 元数据字段。

候选结果为结构错误 0、预期字段命中 49/58、不匹配字段 9、明确排除误触发类型 3，20 个底层业务文件保持不变。

## 实测案例：把测试自动化操作手册重构成流程型 Skill

Skill Forge 将包含大量教程、固定值、重复规则和历史命令的测试自动化操作手册，重构为更短的流程型 Skill。候选保留全部四种场景和浏览器组合。

| 规模指标 | 原版 | 候选版 | 变化 |
|---|---:|---:|---:|
| 运行时文件 | 5 | 3 | **减少 40.0%** |
| 总行数 | 643 | 212 | **减少 67.0%** |
| 总字节 | 44,192 | 13,211 | **减少 70.1%** |
| 主 `SKILL.md` | 12,819 B | 8,818 B | **减少 31.2%** |
| References | 28,321 B | 4,393 B | **减少 84.5%** |
| Scripts | 3,052 B | 0 | **减少 100%** |

主要压缩来自 references：原来的 3 个 reference、436 行，变成 2 个按需读取的 reference、94 行。JavaScript 环境检查脚本被删除。

| 重复或易漂移内容 | 原版 | 候选版 |
|---|---:|---:|
| 固定主用例 ID | 7 | **0** |
| 静态 API 接口示例 | 1 组 | **0** |
| 默认 spec 文件名 | 9 | 1 |
| 共享类型声明规则 | 17 | 2 |
| Chrome 规则 | 18 | 6 |
| Firefox 规则 | 23 | 5 |
| 测试 CLI 相关描述 | 40 | 3 |
| `.claude` 固定路径 | 2 | **0** |

候选用“一个规则只有一个权威来源”替代这些重复内容：

- 主用例优先取用户输入，其次读取仓库配置、用例元数据或现有 spec；仍无法唯一确定时停止询问，不猜测 ID；
- API 的名称、参数和返回值只从当前仓库源码获取，不存在或不兼容时在浏览器执行前停止；
- 共享类型声明固定在一个仓库位置，由用例配置引用，消除 push 前后搬移文件的状态链；
- 使用 `TRIAGE -> PLAN -> PREPARE -> BUILD/VERIFY -> SYNC`，构建前等待用户确认，平台写入再单独授权；
- 不再固定 Claude/Codex 安装路径，只保留跨宿主一致的行为和仓库路径。

候选删除了环境检查脚本、大量测试方法教学、固定平台值、历史 CLI 版本、可复制安装命令及原版翻译说明。当前 CLI help 替代重复保存的历史命令。

候选通过结构检查，移除生成的元数据文件并消除未扫描脚本 unknown。正式行为运行结果为 7 条 `not_run`，决策为 **`inconclusive`**。

### 实机运行截图

![实机运行生成的目标流程与设计选择](assets/readme/fetest-target-structure.png)

*运行过程展示目标流程和需要用户选择的设计项。*

![实机运行中的冻结用例审阅与用户修改](assets/readme/fetest-suite-review.png)

*冻结 baseline、构建隔离候选之前，逐条审阅并修改用例。*

## 为什么做成这样

写 Skill 的工具通常败在两处：要么生成一堆散文却无法判断结果是否比什么都不做更好，要么长出一层庞大的合规机制，以致没人能跑完一次完整流程。

Skill Forge 的立场很窄：

- **验收用例和候选在不同上下文里冻结。** 同一个 agent 两件事都做，会把实现方案泄漏进它写的测试里；单靠先后顺序防不住，因为泄漏发生在冻结之前。
- **观测不到不等于失败。** 真实宿主返回的是散文，所以精确 stdout 类期望记为 `not_run`，绝不记 `failed`；传输故障记为 `infra_error`。一次网络抖动不该产出任何关于候选的判决。
- **停止是一等结果。** `keep_baseline` 和 `no_skill_supported_for_selected_cases` 同样需要证据，和采纳一视同仁。
- **声明上限写在报告字段里，不写在散文里。** 每份报告都标明自己的范围：仅限选定用例、未安装、未发布，以及在适用时加上 `fixture_host_only` 和 `no_held_out_cases`。

## 安装

从经过验证的宿主分发包中取出 `skill-forge/` 目录，作为 Skill 安装：

```bash
cp -R <verified-skill-root> ~/.claude/skills/skill-forge     # Claude Code
cp -R <verified-skill-root> ~/.codex/skills/skill-forge      # Codex
```

作为 Claude Code 插件，先构建插件树，再放到 marketplace 期望的位置：

```bash
python3 scripts/package.py build --source . --host plugin \
  --output <plugins>/skill-forge --manifest <plugins>/skill-forge.manifest.json
```

用 `/skill-forge` 或 `$skill-forge` 显式调用。它不会从普通对话里自己选中自己。

如果想发行一棵经过校验的树而不是直接拷源码，见[打包](#打包)。

## 工作流

```text
NEED -> FRAME -> FREEZE -> DRAFT/STAGE
     -> CHECK -> RUN -> SCORE -> DECIDE
     -> CAUSAL ITERATION -> HANDOFF
```

先选一种可执行模式：`create`、`optimize` 或 `no_skill`。如果已有相邻 Skill 足够，停在 `reuse` triage，不要重复创建。然后在不抄用户实现方案的前提下冻结 goal card，冻结用例集，之后才开始构建。

完整指令在 [SKILL.md](SKILL.md)。参考文档按需加载：

| 参考文档 | 什么时候读 |
|---|---|
| [authoring.md](references/authoring.md) | 起草或修改候选之前 |
| [evaluation.md](references/evaluation.md) | 设计用例或解读判决之前 |
| [evidence.md](references/evidence.md) | 写用例集或解读报告之前 |
| [hosts.md](references/hosts.md) | 跑真实 Codex / Claude 用例之前 |
| [risk.md](references/risk.md) | Skill 含脚本、写文件或涉及凭据时 |

## 三段隔离

写用例的 agent 从没见过候选，写候选的 agent 从没见过密封用例：

```text
主对话        澄清目标 -> requirements.md          （禁止讨论实现）
    |  只传 requirements.md
用例 subagent  suite.json + suite.sealed.json      （必须追问近负例）
    |  只传 requirements.md + suite.json
构建 subagent  候选本体
```

holdout 靠"不把文件传下去"来落实。没有需要绕过的访问控制契约，也不存在事后改标签把 visible 用例变成 sealed 用例的操作。

同一个模型的两个 agent 共享盲区，所以隔离能防污染，防不了无知。用户逐条审阅用例是唯一的异质检查，不能省。用例 agent 还必须问一次：**哪种请求容易被这个 Skill 错误地抢走？** 这个答案只有用户有。

## 用例集

每个 track 一个严格 JSON 文件，在观察候选之前冻结。baseline 和候选路径由 runner 传入，绝不写进用例集。

```json
{
  "version": 1,
  "skill": "canonical-json",
  "mode": "optimize",
  "reps": 1,
  "cases": [
    {
      "id": "core-canonicalize",
      "source": "observed",
      "plane": "execution",
      "category": "core",
      "critical": true,
      "prompt": "Canonicalize payload.json into output.json.",
      "fixture": "cases/core-canonicalize",
      "expectations": [
        {"kind": "json_equals", "path": "output.json", "value": {"a": 1}}
      ]
    }
  ]
}
```

每个用例都要带来源标签 `source`（`observed`、`user_confirmed`、`synthetic`、`assumed`）和类别 `category`（`core`、`boundary`、`failure`、`near_negative`）。critical 的执行用例必须有一个强确定性期望，只给 `contains` 会被拒绝。

`plane: routing` 的用例测的是宿主在不被点名的情况下会不会选中这个 Skill，所以加载器会拒绝提到 Skill 名字的路由 prompt。

## 命令

```bash
# 结构、卫生、AST 风险提示
python3 scripts/check.py <candidate> --suite <suite.json>

# 把原始观测写进一个全新的 run 目录
python3 scripts/run.py --suite <suite.json> --configuration candidate \
  --skill-root <candidate> --host fixture --runs-dir <runs>

# 把 run 归约成报告；密封证据是独立 track
python3 scripts/score.py --suite <suite.json> --run <run> \
  [--sealed-suite <suite.sealed.json> --sealed-run <sealed-run>] \
  --output-dir <report>
```

`run.py` 只记录观测。`score.py` 的每个判决都从这些原始字节派生；调用方无法把 `passed` 写进结果。

## 宿主到底能观测到什么

一个期望只有在所选宿主真能观测到时才决定用例结果。用例集保持宿主中立，可观测性由评分器从 run manifest 解析。

| 期望类型 | `fixture` | 真实宿主 |
|---|---|---|
| `json_equals`、`file_sha256`、`validator`、`file_exists` | 客观 | 客观，需要 `--policy workspace-write` |
| `stdout_contains`、`stdout_not_contains` | 客观 | 仅供参考，不决定用例 |
| `stdout_equals`、`exit_code`、`selected_skill` | 客观 | 不可观测，记为 `not_run` |

当前内置通用 runner 的 `read-only` 模式不能产出工件证据，一个 workspace-write 宿主路径尚未实现，路由 telemetry 也不可用。预期路径是受支持的 workspace-write 宿主配合工件类期望；独立插桩的真机实验单独记录。

fixture 宿主重放冻结好的响应。它能证明管道可用，但不能证明任何关于真实模型的事——所以它的报告都带 `fixture_host_only`。

## 怎么读报告

来自 `fixtures/optimize` 的真实输出，其中候选在核心用例上发生了回归：

```text
Decision: `keep_baseline`

| Case                   | Plane     | Critical | Results                           |
| core-canonical-bytes   | execution | yes      | baseline=passed, candidate=failed |
| duplicate-key-boundary | execution | yes      | baseline=failed, candidate=passed |

## Claims

- Proven: fixture_pipeline_completed_on_selected_cases
- Disproven: candidate_gain_on_selected_cases
- Unverified: generalization_beyond_selected_cases, long_term_stability, runtime_safety,
  host_activation, behavior_on_held_out_cases
- Not run: automatic_routing
```

一个用例改善了，但一个 critical 用例回归了，于是增益声明被 disproven，而不是被平均掉。`Proven` 在 fixture 宿主上只能退到"管道跑通"这一条；`behavior_on_held_out_cases` 是 unverified，因为这次没提供密封用例集。

真实宿主的报告会多出一节，指名哪些没法判断：

```text
## Not observable on this host
- `core-sort` / `stdout_equals`: a live host returns assistant prose, not exact task stdout
```

判决取值只有：`handoff_candidate`、`reject_candidate`、`adopt_candidate_for_selected_cases`、`keep_baseline`、`no_skill_supported_for_selected_cases`、`no_skill_not_supported_for_selected_cases`、`inconclusive`。

密封结果调整声明，不替换判决：一致则证明 `visible_decision_reproduced_on_held_out_cases`，矛盾则推翻该条声明，而 visible 判决依然成立。矛盾是泛化能力的真实界限，不是悄悄给候选重新分类的理由。

## 打包

三种目标：

```bash
# skill 根目录，放进 ~/.codex/skills 或 ~/.claude/skills
python3 scripts/package.py build --source . --host codex \
  --output <dist>/skill-forge --manifest <dist>/skill-forge.manifest.json

# Claude Code 插件，skill 嵌在 skills/skill-forge/ 下
python3 scripts/package.py build --source . --host plugin \
  --output <dist>/skill-forge-plugin --manifest <dist>/skill-forge-plugin.manifest.json

python3 scripts/package.py verify --candidate <dist>/skill-forge \
  --manifest <dist>/skill-forge.manifest.json --output <dist>/skill-forge-receipt.json
```

`codex` 和 `claude` 目标把 `SKILL.md`、`VERSION`、`LICENSE`、`references/`、`scripts/` 放在树根，Codex 额外带 `agents/`。`plugin` 目标把这份载荷移到 `skills/skill-forge/`，并在插件根加上 `.claude-plugin/plugin.json` 和两份 README——这是已安装的 Claude Code 插件的实际布局。

fixtures、tests 和打包脚本本身都是创作输入，永不发行。

receipt 的 `claim_cap` 是 `byte_binding_only`。它证明发行树与 manifest 一致、POSIX 写位已移除。它不是签名，不安装任何东西，也不证明宿主加载了这个 Skill。

## 目录结构

```text
skill-forge/
├── SKILL.md              agent 遵循的工作流
├── .claude-plugin/       plugin.json，供 plugin 目标使用
├── references/           按需加载的细节，只有一层
├── scripts/              check、run、score、package
├── fixtures/             create / optimize / no-skill 管道 fixture
└── tests/                覆盖四个脚本的 70 项回归
```

```bash
python3 -m unittest discover -s tests -v
```

## 边界

直说，因为报告里也是这么写的：

- 内置通用 runner 目前不能产出真实宿主的正面执行证据（见上面的可观测性表）；独立插桩的真机实验仍可在其记录范围内成立。
- 路由和近负例触发行为无法测试，这类用例保持 `not_run`。
- 摘要用于标识被比较的字节、避免结果混用。它们不是签名，对拥有同一文件系统写权限的人毫无防御作用。
- `check.py` 的 AST 发现只是规划提示。没有发现不等于安全证明；非 Python 脚本记为未扫描，而不是记为通过。
- 用例集全通过只覆盖选定用例。泛化能力、长期稳定性、宿主激活和运行时安全按设计保持未验证。
- 交付止于交接。安装是用户自己执行的 `cp -r` 加一个 `.bak`。

## 许可

MIT-NonCommercial，见 [LICENSE](LICENSE)。
