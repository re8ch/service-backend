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

## 2026-09-24 实测：开放 PR 支持保留候选 SHA

用户已批准并创建私有 `re8ch/gitops-spec-lab`。初始本地签名配置缺失导致首个 commit 失败；管理员完成 SSH signing 配置，在 bootstrap 阶段暂时禁用新建的 lab 规则以推送初始提交，然后重新启用。以下测试全部发生在规则 active、bypass_actors=[]、current_user_can_bypass=never 之后。

- 基线：`d021f93b6a5daca87dd8942ec64acab1ad7ea76e`。
- 无 PR 的签名单父候选 `9474768407c5dcd94d0b617244f971566237f6c7` 被 GH013 拒绝，原因必须经过 PR。Rule suite `4196364974` 为 fail。
- 有开放 PR #1 的签名单父候选 `07daded6f0d93b432119c44604400c97d27873b1`，使用显式旧 SHA 的 force-with-lease push 成功，main 保留完全相同的候选 SHA，未生成 merge/squash/rebase commit。
- GitHub 自动将 https://github.com/re8ch/gitops-spec-lab/pull/1 标记 MERGED，mergeCommit 为该候选 SHA。Rule suite `4196375753` 为 pass，actor 为 Woomou；签名 verified=true。
- 规则集 `23891051` 保持 active：required PR、签名、线性历史、禁止非快进、禁止删除，无 bypass。审批人数为 0，尚未加入 required checks。

结论：此前关于原生 required-PR 与精确 SHA push 可能不兼容的疑点，在这个受限配置下已被实际反例消除。仍未证明指定 App 的 required checks、人工批准、受限 App 接纳与并发竞争；不能将本次个人身份结果推广成 production 完整接纳。

### 指定 App 的 required checks

同一规则集新增 `lab-contract` 和 `lab-origin-proof`，均绑定官方 `github-actions` App ID 15368，strict=true，do_not_enforce_on_create=false。

- 候选 d87ffb8c2a7b5ef3ec6b0491348151fe9dd991a0 缺少 origin-proof 时被拒绝（rule suite 4196403646）。
- Woomou 通过 commit status API 写同名 success 后，仍被拒绝：not set by the expected GitHub app（4196406434）。PR checks CLI 显示 pass 不代表绑定规则已通过。
- 作者将尚未接纳的实验提案改为产生两个实际 Actions jobs，重新签名为 0ad3b2165360c206575890ac6d3723af7ab13b42，父 SHA 仍是 07daded6f0d93b432119c44604400c97d27873b1。仅实验分支使用显式旧 SHA lease 更新；主线未改写。原候选的失败证据保留在 rule suites。
- https://github.com/re8ch/gitops-spec-lab/actions/runs/35893493615 的两个 job 成功后，精确 SHA push 通过（4196418405），没有禁用或删除 required checks。

这是来源绑定实验，不是可信验证器实验：fixture 本身来自候选 PR，因此不能证明候选无法修改自己的检查。生产必需检查仍需由受保护的固定验证器生成。接纳主体仍是 Woomou，尚未验证专用 App 的写入身份。

### 真实并发接纳

开放 PR #3 和 #4 均基于 `0ad3b2165360c206575890ac6d3723af7ab13b42`，各自 GitHub Actions 的两个来源绑定检查都通过。两个独立 git push 进程并发使用同一显式旧 SHA lease：

- A：`c61cccc451e6d5c8e8ccc9b855413994345f2335`；PR https://github.com/re8ch/gitops-spec-lab/pull/3 。
- B：`444c6912c6fffd9f2579609669d390f88eb62e0c`；PR https://github.com/re8ch/gitops-spec-lab/pull/4 。
- B 退出 0；A 退出 1，服务端错误 `cannot lock ref`，实际 main 已是 B、expected 仍为原父。
- API 读回 main 精确等于 B，没有组合 A/B 的新提交，没有重试合并失败候选。
- 两次 rule suite 均为 pass（A 4196443473，B 4196443205）：规则通过不代表 ref transaction 成功。必须结合传输结果和权威 ref 读回判定接纳；丢失回执时也不能只查规则审计。

此结果证明 GitHub 托管的同父竞争保持单一赢家；身份仍是 Woomou，尚非专用 App。失败候选保留为过期提案，不自动 rebase。

### 固定验证器来源，避免候选自证

组织规则 `23891300 spec-lab-pinned-validator` 仅匹配 lab repository ID `1383863668` 的 `refs/heads/main`，active、无 bypass。required workflow 固定为该仓库 `.github/workflows/lab-contract.yml` 的完整 SHA `0ad3b2165360c206575890ac6d3723af7ab13b42`，不跟随候选或分支。首次创建因源仓库未允许 Actions 共享返回 422；仅将私有 lab 的 Actions access 设置为 organization 后创建成功，未改变生产规则。

PR https://github.com/re8ch/gitops-spec-lab/pull/5 的签名候选 `1c94a4d16922548bd62cbe38a24292602fcb5165` 替换两个 job 的 echo 为 `CANDIDATE_REPLACEMENT_MUST_NOT_REPLACE_PINNED_VALIDATOR`。实际运行 https://github.com/re8ch/gitops-spec-lab/actions/runs/35893933294 ：

- job `107293019160` 日志仍执行原始 `Protocol fixture only; no deployment authorization.`。
- job `107293018844` 日志仍执行原始 `Expected-origin fixture; no deployment authorization.`。
- 两个 job 成功。run 的 head_sha 指向候选、referenced_workflows 为空，不能据此判断验证器来源；本次通过 jobs logs API 验证实际执行内容。`gh run view --log` 因内部 workflow ID 查询 404 失败，不代表运行失败。

结论：此配置下，组织 required workflow 的固定 SHA 没有被候选同路径修改替换。此试验只证明代码来源固定，不证明 fixture 已实施权限检查；候选仍可提出修改，真正的生产路径/资源所有权拒绝需要可信验证器实现。PR #5 保持开放，尚未接纳到 main。后续仍须验证删除工作流、同名 job 冒充、固定验证器安全读取候选数据，以及专用 App 身份下的接纳。
