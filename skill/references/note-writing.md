<!-- Encoding: UTF-8 (no BOM). Windows PowerShell 5.1: Get-Content -Encoding UTF8 <file> -->

# 參考：擴寫筆記

任務是「把骨架筆記擴寫成完整筆記」時，讀這一份。

## 規範在哪裡

唯一的規範文件是 **[`docs/note-writing-guideline.md`](../../docs/note-writing-guideline.md)**，目前版本 **`guideline_version: 1.4`**。

這份參考不重寫規則。規則只有一份，重寫兩份就會分岔。下面只寫流程。

## 流程

1. **拿指令包**

   ```bash
   l2n render --expand-prompt <stem>.json
   ```

   它會印出規範版本、規範全文（或路徑）、要讀的三個檔案（`<stem>.v4.md`、`<stem>.srt`、`<stem>.json`）、生效的 style 與讀者層級（`note.reader`），最後印出完成後要跑的驗收指令。profile 開了 `references.verify_citations`／`record_handout` 時，還會多印文獻查證與講義記錄的步驟。

2. **先做 Step 0 分流**（規範 §0.1）

   填得出「問題／方法／差異」三格才做完整擴寫；填不出來就停在骨架加一段檢索摘要。不是每一場講座都值得一份完整筆記。

3. **讀齊素材**

   官方講義 > 用眼睛看過的影格 > 逐字稿 > OCR。OCR 只拿來決定要打開哪一張影格，**不可以抄進筆記**（規範 §1）。

4. **就地擴寫 `<stem>.v4.md`**

   章節順序與 frontmatter 不得更動。原話包「」附時間碼；不可原句直貼；講者說「我沒有經驗」就照錄，不可代答；ASR 錯字本文改對、References 列表（規範 §2）。姓名處理見規範 §3。

   **先寫 Evergreen：一句話總結本課程內容**，指得回時間碼（規範 §4.1）。逐條對一次規範 §2.5 的正確性清單——說過頭、夾帶因果、替講者排順序是最常見的三類。本文自己的推論只認 `[推論]`、`[本筆記整理]` 兩種記號，沒標就是編造。讀者層級不是 `self` 時，名詞小辭典也要寫（規範 §0.11）。

5. **驗收（必做）**

   ```bash
   l2n check note <stem>.json --note <stem>.v4.md --style faithful
   ```

   `--style` 要與當初 `l2n render` 用的一致。不給時，`check note` 會讀筆記
   frontmatter 之後那一行 `<!-- l2n:style=... guideline=... -->`——那是 `render`
   寫下的單一真相；兩者不一致會印 `warn style mismatch`。
   **不要刪掉那一行**：刪了之後 `check note` 會退回 profile 預設（concise），
   faithful 才檢查的 R6 就整個不跑。

   報告第一行會印規範版本。**有任何 error 就回頭修，不要交出去。** exit code：0 全過、1 只有 warning、2 有 error。

   結尾還會多兩行數字（不影響 exit code，但它們是「有沒有人真的動過」的唯一證據）：
   `note: ai_draft_remaining=N` 是還沒換掉的 `<!-- ai-draft -->` 佔位；
   `note: unexpanded_segments=K/N` 是本文仍有 60% 以上字元來自 `l2n render` 骨架的段落數。
   **兩個都要是 0，才算擴寫完成。**

6. **獨立驗收（交出去之前）**

   ```bash
   l2n review-prompt <stem>.json --note <stem>.v4.md
   ```

   `check note` 抓不到「說過頭」「夾帶因果」這一類錯。把這份交辦書交給**沒參與撰寫**的人或 agent：

   - 驗收者**唯讀**，自己回逐字稿與講義抽來源，自己寫檢查方法，不沿用撰寫者的草稿或腳本。
     撰寫者自己派的 read-back 不能取代這一步。
   - 交辦書已列好抽查候選：本文的數字與所屬段落時間區間、含 decade 的段落、至少 8 句「」引文、R12 context 層命中。
   - 回報 `PASS` 或 `需修正`；修正清單依嚴重度排序，每條附行號、原字串、建議改法、時間碼。
   - 修正清單**交回原撰寫者**改，逐條回報狀態。
   - 改完由主對話用 grep 抽查每一個原字串確實消失，再跑一次 `l2n check note`。

   環境不能派 agent 時，就由使用者本人或另一個模型的新對話擔任驗收者——重點是「沒參與撰寫」。

## 做完了沒有：R10 與兩個數字

`l2n render` 剛產出的骨架，R1 到 R9 全部都會過——**它們檢查的是形狀，而骨架的形狀本來就是對的。**
所以還有 R10：把筆記每一段的本文，和「用同一份 JSON、同一個 style 重新 render 出來的骨架」
比對，算出本文有多少比例的字元仍然逐字來自骨架，**達到 60% 就報**（門檻可由 profile 的
`check.r10_ratio` 改；只在骨架前面加一句、原文一字沒刪，照樣會報）

```
warn R10 segment 3: unexpanded skeleton (82% of the body is render output)
```

Evergreen 還是骨架那一句時同樣會報。**骨架直接拿去 check 會每一段都報 R10，那是預期行為。**

判斷「擴寫完成了沒有」只看結尾那兩行：

```
note: unexpanded_segments=0/6
note: ai_draft_remaining=0
```

**兩個都是 0 才算做完。** 只填講者骨架與題目答案、其餘原封不動，是實測出現過的失敗樣態。

## 最容易犯的三個錯

| 錯 | 機器會不會抓到 |
| ---- | ---- |
| 把逐字稿句子原封不動搬成條列 | 會（R5，40 字以上且未標示為引用） |
| 沒素材的章節填 `N/A` 或補教科書套語湊字數 | 只抓得到字面的補位字串（R8）；套語抓不到，靠自己 |
| 講者說「我不確定」，模型自己查資料補上答案 | **抓不到**。這是最嚴重的一種污染，只能靠規範 §2.3 |
| 講者說「通常」，筆記寫「一定」；相鄰兩句被接成因果 | **抓不到**。規範 §2.5，交給 `l2n review-prompt` 的獨立驗收 |
| 譯名不是台灣用法（蛛網膜、頜、thrombosis 譯成栓塞） | radiology profile 會抓（R12，清單在 `outputs.toml`）；generic 不帶清單 |

1.4 新增的三條機器規則：R12 禁用譯名、R13 影格說明裡的「推測／推斷」、R14 名詞小辭典
（`student`／`public` 讀者缺這一節，或小辭典裡寫了數值閾值）。R5、R8 也開始掃 `# 閱片`；
R9 要求每一題都有自己的 `> [!answer]-`。細節見規範「機器可檢查」。

## 不歸這裡管

- 骨架章節怎麼來的 -> `l2n render`，見規範開頭。
- 影格怎麼抓、JSON 怎麼長 -> 各自的階段參考。
- 讀者自己該做的消化步驟（保留／刪除／改寫「我應該記住的 3 件事」）-> 規範 §0.6，模型不代勞。
