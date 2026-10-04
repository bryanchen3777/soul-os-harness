
# TA-2 v2-E — E2 Engineering Resolution

> 狀態：ENGINEERING RESOLUTION — DESIGN REVISION ONLY
> 前置：E Design blocked at E2；E-D1 / E-D2 canonicalized in 8ad25c2
> 目的：解決 evaluator information boundary 與 cross-section measurement consistency
> 禁止：不跑 LLM、不寫 runtime、不開 E-Pilot、不選 threshold、不重開 A-v2、不新增 Rule 6+

## 1. Engineering conclusion

E2 的核心錯誤不是「A/B label 不夠 opaque」。

真正需要 blind 的不是 response 裡是否存在 temporal evidence；那個 evidence 本身就是 measurement object 的一部分。

真正必須 blind 的是：
- response 是由哪一個 temporal context 生成；
- trial 被標成 CORRECT / MISMATCHED / OFF / IRRELEVANT 哪一種 condition；
- 哪一個答案被預期為 correct direction；
- generation-side source→condition mapping；
- acceptance / threshold information。

因此 evaluator 不再被要求判斷「哪個 response 是 CORRECT / MISMATCHED」。

Evaluator 只做 source-blind semantic relation judgment：

> 這個 response 的 interpretation，比較符合 temporal context A、context B，還是無法區分？

Evaluator 可以看到候選 temporal contexts A/B，但不知道 response 的 generation source。

這使 direction 有定義，同時不把 expected direction 當 answer key 傳給 evaluator。

## 2. Information boundary

Evaluator MAY see：
1. Stimulus
2. Candidate temporal context A
3. Candidate temporal context B
4. One generated response
5. Fixed evaluation instructions

Evaluator MUST NOT see：
1. response 的 generation condition
2. source context assignment
3. CORRECT / MISMATCHED label
4. OFF / ON label
5. IRRELEVANT / RELEVANT label
6. internal stimulus metadata
7. exact timestamp metadata
8. expected answer / expected temporal direction
9. threshold / acceptance criteria
10. decoding map

Response 中出現「午餐」「宵夜」「晚上」等 temporal evidence 不是 information leakage；它是被測量的 observable。

Leakage 是 measurement system 主動把 hidden source、expected direction 或 condition identity 提供給 evaluator。

因此 evaluator 能「猜到」source，不等於 blinding failure。真正 requirement 是：

> Evaluator 必須能在不知道 source assignment 的情況下，獨立判斷 response 與 candidate contexts 的 semantic relation。

## 3. Scoring primitive

舊設計撤銷：

Response A vs Response B → 哪一個比較符合 expected temporal direction。

新設計：

Response R vs Context A / Context B → 哪個 context 更能解釋 R 的 temporal interpretation。

Evaluator output：
- A
- B
- TIE
- AMBIGUOUS

A/B 顯示順序每 trial randomize。

這仍然是 pairwise scoring primitive，但 pairwise 發生在 response ↔ temporal-context candidates，而不是 response ↔ response / expected-answer key。

## 4. Per-trial measurement

Evaluator output 轉成同源 observable：

direction ∈ {A, B, TIE, AMBIGUOUS}

Attribution strength：
- A / B preference → directional attribution present
- TIE → no directional attribution
- AMBIGUOUS → insufficient evidence；不得當作 negative evidence

Aggregation 不把 AMBIGUOUS 當成 0。

## 5. Hidden source mapping

Generation layer 保存 response_id → source_context。

例如 R17 → Context A。

Evaluator 完全看不到 mapping。只有 aggregation layer 在 evaluator scoring 完成後才 decode。

因此可以產生 secondary directional validation：
- response generated under Context A 是否 preferentially attributed to A？
- response generated under Context B 是否 preferentially attributed to B？

這是 directional validation evidence，不是 evaluator 的 answer key。

## 6. Resolve the OFF problem

OFF 不再被硬塞進 response-vs-response pairwise comparison。

OFF 使用完全相同的 evaluation protocol：
- stimulus
- candidate Context A
- candidate Context B
- OFF-generated response
- evaluator 不知道這是 OFF response

因此 OFF 是 generation condition，不是 evaluator-visible label。

OFF response 仍可以被 evaluator attribution 到 A/B/TIE/AMBIGUOUS。

這讓 OFF 進入同一個 per-trial observable，並測量沒有 temporal context 注入時的 temporal attribution / generic coloration。

## 7. Replace the broken causal layout

原本的 CORRECT − MISMATCHED 與 ON − OFF 在 measurement object 上不對稱。

工程修正：

Factor 1 — Temporal context availability
- OFF
- ON

Factor 2 — Event relevance
- RELEVANT stimulus
- IRRELEVANT-control stimulus

Primary outcome：
temporal attribution strength。

Primary causal estimand：
ΔR = RELEVANT_ON − RELEVANT_OFF

ΔI = IRRELEVANT_ON − IRRELEVANT_OFF

DiD = ΔR − ΔI

這保留 E-D1 的 DiD causal estimand，同時讓：
- 同一 outcome
- 同一 evaluator protocol
- 同一 scoring scale
- 同一 generation/evaluation boundary
- 只有 factor 改變

因此 DiD 不再依賴 pairwise 裡不存在的 OFF。

## 8. CORRECT / MISMATCHED 的新位置

CORRECT / MISMATCHED 不再是 evaluator-facing scoring categories。

若仍保留於 stimulus design metadata：
- 只描述 stimulus-context relation；
- evaluator 不可見；
- 不直接進 evaluator scoring；
- 不作為 answer key。

用途降為 secondary analysis of directional behavior / semantic congruence。

它不再承擔 primary causal estimand。

## 9. Cross-section consistency audit

Construct：
Temporal Context Sensitivity：temporal context 改變是否造成 event/situation interpretation 的可觀測改變？

↓

Scoring object：
Response 與 candidate temporal contexts 的 semantic relation。

↓

Per-trial observable：
A / B / TIE / AMBIGUOUS。

↓

Condition aggregation：
同一 observable 在 RELEVANT × ON/OFF 與 IRRELEVANT × ON/OFF 中分別聚合。

↓

Causal estimand：
DiD = (Relevant_ON − Relevant_OFF) − (Irrelevant_ON − Irrelevant_OFF)

↓

Threshold readiness：
只在取得 effect magnitude、natural evaluator/generation noise、irrelevant-control baseline、replication stability 後進入 threshold derivation。

每一層現在都能產生下一層需要的輸入。

## 10. What this does NOT claim

本 resolution 不宣稱：
- evaluator 一定可靠；
- LLM semantic judgment 一定比 A-v2 regex 好；
- TCS 已被證明；
- DiD 一定會顯著；
- threshold 已存在；
- E-Pilot 已獲授權。

它只解決 E2 information boundary + measurement-chain incompatibility。

## 11. E-Pilot preconditions

尚未執行，只列為 instrument validation targets：
1. Order bias
2. Context lexical bias
3. Forced-choice bias
4. Evaluator instability
5. Source reconstruction
6. OFF coloration
7. Relevant / irrelevant separation

Source reconstruction 特別注意：evaluator 從 response 推測 source 不自動等於 failure；只有當 source reconstruction 取代 semantic relation judgment，才是 measurement concern。

## 12. E2 gate resolution

PASS preconditions：
- source assignment hidden
- condition labels hidden
- expected direction hidden
- threshold hidden
- evaluator receives same semantic information class across ON/OFF
- candidate context order randomized
- decoding occurs only after evaluator output is frozen

E0 consequence：
Construct 不再依賴 correct-direction answer key。

E1 consequence：
DiD 現在由同一 per-trial observable 產生：
(Relevant_ON − Relevant_OFF) − (Irrelevant_ON − Irrelevant_OFF)

E3 consequence：
Primary unit = stimulus × condition × generation response。
Replication 不當作新的 independent stimulus。
Evaluator reruns 是 evaluator-layer replications，不增加 generation-level N。

## 13. Status

本文件完成 E-DESIGN-REVISION / E2 RESOLUTION，不是：
- E-Pilot
- implementation
- threshold selection
- TA-2 acceptance

目前：
- Gate 2 = INCONCLUSIVE
- A-v2 = STOPPED
- E-Pilot = NOT AUTHORIZED
- Threshold = NOT DEFINED
- Runtime = UNCHANGED

下一步不是直接跑實驗；應先對本 engineering resolution 做 design-contract review，確認是否仍存在 information leakage、construct circularity 或 cross-section incompatibility。
