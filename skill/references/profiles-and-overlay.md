# 參考：profile 與 overlay

任務是「換詞庫」「改 frontmatter 模板」「打開或關掉某個輸出」或「使用者說他有自己的
設定檔」時，讀這一份。

## 兩個概念

- **profile**：套件內建的一整組設定。`generic` 是預設；`radiology` 內建但不預設啟用。
- **overlay**：使用者自己的設定檔，疊在 profile 之上。程式碼裡沒有「某位使用者專屬」
  的分支，所有個人化都走 overlay。

## 解析順序

由高到低，先命中的贏：

```
CLI 參數 > 專案內 .lecture2notes/ > 家目錄 ~/.lecture2notes/ > profiles/<name>/ > 套件內建
```

`corrections.json` 是**合併**（高層蓋掉同一筆 `heard`），其餘檔案是整檔覆寫。
某一層沒有那個檔就跳過該層，不是錯誤。

## 五個可覆寫的檔名

| 檔名 | 管什麼 |
|---|---|
| `note.frontmatter.yaml` | 筆記 frontmatter 模板（欄位與順序） |
| `note.template.md` | 骨架筆記的章節模板 |
| `corrections.json` | 錯字對照表（合併） |
| `outputs.toml` | pbf／hub／viewer 開關、`note.style` 預設 |
| `privacy.toml` | 個資比對樣式，`check note` 會用 |

檔名是固定的，拼錯不會報錯，只會安靜地不生效——改完一定用 `l2n profile show` 確認。

## 看目前生效值

```bash
l2n profile show
l2n profile show --profile radiology
```

會印出每一項的最終值與它來自哪一層（cli／project／user／profile／builtin）。
使用者抱怨「我明明改了設定卻沒作用」時，第一件事就是跑這個，不要憑猜。

## 建立 overlay

在專案資料夾建 `.lecture2notes/`，或在家目錄建 `~/.lecture2notes/`，把要改的檔案
放進去即可；不需要整組複製，只放要覆寫的那幾個。

`l2n install-skill` 部署 skill 時**不會**動目標目錄裡既有的這五個檔名，
所以更新 skill 不會蓋掉使用者的 overlay。

## 個資與敏感素材

院內或病患素材的判斷見 SKILL.md HARD RULE 5。`privacy.toml` 只是機器兜底，
抓到就是 error；沒抓到不代表安全。

## 不歸這裡管

- 筆記內容規範 -> [note-writing.md](note-writing.md)
- 哪些輸出怎麼產 -> [outputs-and-batch.md](outputs-and-batch.md)
