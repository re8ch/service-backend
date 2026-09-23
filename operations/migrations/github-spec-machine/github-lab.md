# GitHub 托管接纳试验

这是执行前清单，不表示试验已完成。目标仓库必须是专用私有 lab，禁止使用 service-backend、任何 archive 或现有产品仓库。建议名 `re8ch/gitops-spec-lab`，等待用户确定。不能携带生产 manifest、Secret、registry 凭据或集群连接。

## 配置与身份

管理员仅做初始建库/安装/规则配置，不充当成功接纳的身份。记录继承的组织规则和最终有效规则；实验 main 启用签名、线性历史、禁止非 fast-forward、required PR 和指定 App 的检查。普通操作者无 bypass。初始配置使用 reviewed ruleset payload，禁止临时删规则让测试通过。

用单一专用 GitOps App installation token 完成操作；初始权限只给 Contents write、Checks write、Pull requests read、Metadata read。若试验进入 Deployment，再增加 Deployments write。身份仅安装到 lab；不复制现有 Flux 私钥、不使用管理员 token 模拟 App。App 创建/安装属于显式管理步骤，Actions 不持有 Administration 权限。

每个案例记录前后 ref、候选父/tree、PR ID、check App ID、规则集 ID/摘要、调用主体、HTTP/Git 退出码、时间和 Actions URL。错误响应脱敏。API 成功但 SHA/tree 不符判为失败。

## 逐项验证

| 案例 | 操作 | 必须观察到的结果 |
| --- | --- | --- |
| 正常精确接纳 | 签名单父候选 H 的父为 main B；创建 PR；专用 App 写检查；尝试接纳 H | 若规则拒绝直接更新，如实记录原生 PR/接纳冲突；不得调用 squash/rebase 绕过 |
| 同父竞争 | 两个签名候选 A/C 都以 B 为父，各自有效 PR/检查；并发提交显式 B 租约 | 仅一个成功，main 为已验证的原候选 SHA，没有生成新提交 |
| 过期许可 | main 已推进后，再提交绑定 B 的许可 | 拒绝；不能自动吸收新 main |
| 未经 PR | 同样有效候选但无 PR | 拒绝 |
| 无检查/伪造来源 | 无检查、同名另一 App 检查、检查只属于旧候选 | 全部拒绝 |
| 未签名/非线性 | 未签名候选、merge commit、无关根提交 | 全部拒绝 |
| 内容越权 | 试验服务 A 候选修改 B 的资源或 platform policy | 平台检查拒绝；改动自身检查器不能使其通过 |
| 前序失效 | 接纳 A 后先写入签名失效记录 R，再尝试接受以 A 为父的 C | C 失败；R 和历史保留；不直接 reset main |
| 执行中失效 | 执行授权发出后写入 R | 后续授权不发放；已执行部分如实记账，不声称追溯撤销副作用 |
| GitHub 故障/重试 | 模拟成功响应丢失、重复 dispatch、读取失败 | 先读回 ref/执行账本确认，不重复制造接纳；失败时不转管理员身份 |

## 不能隐瞒的协议分叉

若 required PR 禁止精确候选 ref 更新，则严格协议尚不成立。可继续研究受限 App 接纳路径，但任何 bypass 必须只影响 required-PR 这一个独立规则，签名、线性历史、非快进与检查规则仍独立执行。App 自身还需证明确实绑定已审查 PR；这是自定义准入逻辑，不再是 GitHub 原生 PR 强制保证。必须向用户明确这种安全边界变化，不能自行配置 production bypass。

若用户坚持完全不增加通用适配逻辑且不接受以上边界，则报告 GitHub 原生能力不满足全部条件，不能将普通 merge queue 或 squash 包装成严格接纳。

## 退出条件

所有案例在 App 身份下得到预期结果才允许设计 production 接纳切换。仅个人 token 成功、仅本地 Git 成功、只有 shadow 工作流绿色均不算通过。lab 试验不批准任何集群写入；后续需要独立验证 bridge 身份、只读 watch 和单一 writer 交接。
