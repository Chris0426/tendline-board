# SPEC-UI-V2 — 前端重設計（1.5／2.0 兩版式、舊版零觸碰）

> **身分**：2026-09-26 Chris 開題「用 impeccable skill 重新設計 Board 所有網頁：AI 味太重、讀者認知成本高；新前端全部完成後由 Chris 親眼看完決定是否上線；舊前端必須保留；色彩使用規則不可破」。本檔＝這次重設計的裁決存證與建置契約。Fable 5.1 裁、opus 建、Fable 親看截圖驗、Chris 拍板。
> **姊妹文件**：`PRODUCT.md`（impeccable 產品真值，2026-09-26 新增）；各頁 `SPEC-*.md` 的鐵則／反模式仍逐頁有效，v2 只換呈現不換口徑；`GOVERNANCE.md` QG-4／QG-5／QG-7 照舊；新頁走 QG-8（2026-09-28）。
> **impeccable 契約存證**：direction seed `e1798e8d`（scope direction／mode operate），骰指定我方 grounded 清單第 4 名＝「警示堆疊＋儀表帶」（EICAS/ECAM 機組警示顯示語法）；六張挑戰牌：夜航六儀表 competitive、其餘五張 declined，各捐一條紀律（§5.3）。Chris 2026-09-26 裁：**骰指定方向＝2.0，常規出口（標準看板做到頂）＝1.5，兩版都做**；B「單線圖」因把生產機台畫成空心、與 C-LD2「空心＝無資料」正面衝突而出局（事實理由非品味）。方向回合以終端問答＋三個靜態 mock（`scratchpad/redesign/mock/`，不進 repo）呈現，未用 impeccable 的瀏覽器決策頁。

## §0 範圍與不變量

- **範圍**：`page-inventory`（§10 附）列出的 22 個使用者頁面，分三批（§8）。`/saltmarsh/weekly`（PPTX 復刻，C-W3 綁 PPTX 色）、`/landing/v1`（已是保留舊版）、`/updown`（已併入 /equipment、無入口）、`/assistant` 對話面（SPEC-HERMES-TRUST 自有規格）**不重做**；它們在 v2 殼下只換 nav（§2.3）。
- **不變量（違反即退件）**：
  1. **口徑零改動**：每個數字、每個狀態、每個「—」的算法與來源不變；v2 只換呈現。頭數字必須與 v1 同頁同請求逐位元相同（§9 驗收 ①）。
  2. **舊 template 零觸碰**：`templates/*.html`（非 `v2/`）與 `_nav.html` 一個位元組都不改；smoke 對舊檔的所有釘照舊全綠。
     - **P0（2026-09-28 Fable 裁，Chris 委裁；KEEP-BATCH3 §H-2）：本條於「UI v2 建置完成」（三批 09-28 全進主線、預設已切 1.5）起失效。** 自此凡屬**行為缺陷**（口徑錯、圖例缺、SPEC 已裁未落地）v1／v2 **同一包同修**、smoke parity 釘照常成立；**純視覺／設計系統題只做 v2**（v1 待退役、不再投資）。這是 R11「改則 v1／v2 同改」的正式化；Chris 若否決 P0，R33／R38／R39 的 v1 半邊退回「只記 ROADMAP」。
  3. **§23 十態、信心 enum、canonical 中文 key** 不變；色彩語意規則（§4）全部保留。
  4. **主題跟隨系統、英文預設＋中文切換、`?source=` 誠實標示、tunnel tier 分層**照舊（GOVERNANCE QG-4）。
  5. **助理能答**（QG-7）：v2 不新增指標，故不新增 MCP 工具；但 v2 頁面的 `page_ctx` 要與 v1 等價，助理在 v2 下答得出的題與 v1 相同。
  6. **主線＝live 執行目錄**：v2 全部在 worktree 建；Chris 驗過才 cherry-pick；上線前 `board_ui` 預設值＝`v1`（§1.3），上線時才改預設。（2026-09-28 Chris 裁上線預設＝1.5，見 §1.3。同日晚 nav 收斂〔KEEP-BATCH3 §H-3、`9c0f419`〕：nav 只剩一顆二態開關 `Laptop｜Projector`，v1 不上 nav、只走 `?ui=v1`、cookie 不寫 v1，見 §1.2。）
  7. **新頁走 QG-8**（2026-09-28 Chris 裁）：新頁只准寫 `templates/v2/`（`{% extends 'v2/_base.html' %}`＋`_c_*` macro）、route 走 `tpl()`，不再寫 v1 形、不新增 v1 模板；過 GOVERNANCE QG-8「新頁四步」。設計系統唯一出處＝`DESIGN.md`（建成物反推）＋本檔附錄 A 規則；改風格＝改這兩檔＋`board.css` token，不是在頁上另起爐灶。

## §1 三個版本與切換

### §1.1 名稱
| 代號 | 內容 | 狀態 |
|---|---|---|
| `v1` | 現行前端，原檔原樣 | ~~保留，不動~~ 保留、**不上 nav**、只走 `?ui=v1`、cookie 不寫 v1（2026-09-28 晚 §H-3） |
| `15`（1.5） | 標準營運看板做到頂（版號理由：資訊結構與版面順序不變，但殼、樣式表、元件、每頁 template 全部重寫＋紀律全面收斂＝比小改版大、未到換結構；Chris 2026-09-26「看差異量定數字」）：現行資訊結構（KPI 列在上、主圖在下、卡片格線），執行紀律拉到頂 | 本輪新建 |
| `2`（2.0） | 警示堆疊＋儀表帶：主圖在上最大、異常按嚴重度收成一疊、無卡片細線分區、數值帶趨勢 | 本輪新建 |

### §1.2 切換機制（一處接線）
- **狀態來源**：~~cookie `board_ui` ∈ {`v1`,`15`,`2`}；`?ui=` 查詢參數同值可覆寫並**寫回 cookie**（一次切、之後都記得）；都沒有 ⇒ 預設值（§1.3）。~~ ⇒ **2026-09-28 晚（KEEP-BATCH3 §H-3）**：`?ui=` ∈ {`v1`,`15`,`2`} 覆寫 → 否則 cookie `board_ui` → 否則預設值（§1.3）。cookie **只寫 `15`／`2`**（`UI_COOKIE_VALUES=("15","2")`）；`?ui=v1` 只管當次請求、不寫 cookie。讀 cookie 仍接受 `v1`（`UI_VALUES` 不變；舊 `board_ui=v1` cookie、smoke 靠它打 v1）。
- **解析點**：`_tunnel_ctx`（app.py:3767）旁新增一個 context processor `_ui_ctx(request)` 回 `{"ui": <v1|15|2>, "ui_href": {...}}`——同 `_tunnel_ctx` 的理由：一處接線、新頁自動有、路由蓋不掉。
- **模板選擇**：新增一個 helper `tpl(request, name)`：`ui in ('15','2')` 且 `templates/v2/<name>` 存在 ⇒ 回 `v2/<name>`，否則回 `<name>`。**每個受影響路由只改這一行**（`templates.TemplateResponse(tpl(request, "x.html"), ctx)`）；htmx body／partial 端點同樣經 `tpl()`。1.5 與 2.0 **共用同一份 v2 template**，差異靠 `<html data-ui="15|2">` 的 CSS 分支與少量 `{% if ui == '2' %}` 模板分支（§5）。
- **切換鈕**：~~v2 nav 右上角三段切換 `v1 · 1.5 · 2.0`（連到當前 URL 加 `?ui=`，保留其餘 query）~~ ⇒ ~~**2026-09-28 Chris 裁：nav 只剩一組三段開關 `1.0｜1.5｜2.0`**（標籤改 1.0、內部值 `v1|15|2` 不動），版式與密度綁定：`1.5`＝v2 標準版式＋laptop 密度（`?ui=15&density=laptop`）、`2.0`＝v2 投影版式＋projector 密度（`?ui=2&density=projector`）、`1.0`＝v1（`?ui=v1`，密度不動）；`?density=` 參數與 cookie 機制保留、無 UI（§6）。三段不是版本階梯而是「舊版待退＋新設計兩版式」，往後改頁都進同一套 v2 設計系統、不會再出 3.0 標籤；驗收後 `1.0` 從 nav 拿掉（`?ui=v1` 留逃生口、v1 模板之後退役）＝下一步、待 Chris 裁。v1 側原條文（09-28 同批 codex 亦動 `templates/_nav.html`，以建成物為準）：**v1 的 `_nav.html` 不改**，所以 v1 使用者回到 v2 的入口＝任何頁面加 `?ui=2`——上線公告時寫明。~~
  ⇒ **2026-09-28 晚（Fable 裁、Chris 口頭定案；KEEP-BATCH3 §H-3、落地 `9c0f419`）：nav 右上角只剩一顆二態開關、字樣用場景詞**——EN `Laptop｜Projector`、ZH `筆電｜投影`（i18n 鍵 `ui_laptop`／`ui_projector`）；群組 `title`／`aria-label`＝`ui_density_tip`，每顆 `<a>` 不另掛 title。`Laptop`＝`?ui=15&density=laptop`、`Projector`＝`?ui=2&density=projector`（`ui_href` 只剩 `15`／`2` 兩項）。`1.0`／`1.5`／`2.0` 數字不再出現在任何畫面上（「1.5／2.0」只留作文件與內部代號）。原「驗收後 `1.0` 從 nav 拿掉…待 Chris 裁」＝**已裁已落地**：v1 不上 nav、只走 `?ui=v1` 網址逃生口（回滾、smoke、對照用）、cookie 不寫 v1。v1 頁的 nav 同樣印這顆開關（兩段皆不亮、無 `aria-current`）、一按即回 v2 並寫回 cookie 15／2 ⇒ v1 使用者回 v2 的入口＝nav 這顆開關（v1／v2 同改屬 P0 行為缺陷類）。用戶反應不佳要加回 v1 ⇒ 把拿掉的那段 `<a>` 貼回兩支 `_nav.html` 即可，其餘機制都在。
- **內外一致**：內部與 tunnel 外部（Saltmarsh Assembly）都能切（Chris 2026-09-26 裁）；tier gate 邏輯不變（閘擋路徑，與 UI 版本無關）。
- **cookie 屬性**：`max_age=365d`、`samesite=lax`、`path=/`；不含個資。

### §1.3 預設值
- **建置期與 cherry-pick 進主線時：預設 `v1`**（零行為變更，live 使用者看不到差異）。
- ~~**上線時預設值由 Chris 裁**（2.0 或 1.5；Chris 2026-09-26 表示「預設 v2」，指上線後）；改預設＝改一個常數 `UI_DEFAULT`。~~
- **2026-09-28 Chris 裁上線預設＝1.5**（原話「這輪就是最後一輪→預設 v2 版，用戶不習慣可以自行切換版本」；Fable 取 1.5）：`UI_DEFAULT="15"`；v1 可切回（~~nav `1.0` 或 `?ui=v1`~~ ⇒ 09-28 晚 §H-3 起**只有 `?ui=v1`**、只管當次請求、不寫 cookie）。

## §2 共用結構（v2 只做一份）

### §2.1 檔案
```
templates/v2/_base.html          唯一的 base layout（{% extends %}），head／nav／main／footer 插槽
templates/v2/_nav.html           v2 nav（分組 tab、source badge、語言、~~三段切換 `1.0｜1.5｜2.0`（09-28 起兼密度）~~ 二態開關 `Laptop｜Projector`（09-28 晚 §H-3；兼密度）、助理與 WebMCP include 照舊）
templates/v2/_c_*.html           共用元件 macro：_c_table（表）、_c_kpi（數值＋趨勢／Δ）、_c_alerts（警示堆疊）、
                                 _c_legend（圖例）、_c_panel（1.5 卡片／2.0 細線區）、_c_scroll（卷軸窗）、_c_tip（tooltip）
templates/v2/<page>.html         每頁殼，extends _base；templates/v2/_<page>_body.html 每頁 htmx body
static/v2/board.css                 唯一樣式表（本專案第一個 CSS 檔）；token 只在這裡定義一次
static/v2/fonts/                 自 host 字型（§6）；不用 CDN（tunnel 外部與離線 demo 都要能載）
static/v2/board.js                  只放必要的無框架 JS（切換、密度、reduced-motion 偵測）；圖仍 inline SVG／CSS
```
- **舊頁的 inline `<style>` 全部不搬**；v2 的 CSS 從 token 重寫。舊頁 25 份重複 token 的問題在 v2 消失。
- **htmx 端點**：所有 `hx-get` URL 與輪詢間隔照舊；v2 只換渲染的 template。任何端點的 JSON／CSV 形不動。

### §2.2 Token（`board.css :root` 一次定義；dark 在 `@media (prefers-color-scheme: dark)` 覆寫）
- **中性層**（1.5 與 2.0 共用）：`--bg --panel --panel2 --line --line2 --fg --mut --mut2 --hover --focus`。**不允許在 template 內寫 hex**（smoke 釘：`templates/v2/**` 零 `#[0-9a-f]{3,6}`，狀態色一律 `var(--…)`）。
- **語意層**（值不變、名字沿用）：`--good --warn --bad --unk --ptag --ptag-ink`；停機四色 `--st-repair --st-pm --st-parts --st-vendor`；`--st-legacy --st-hwait --tex-stripe --st-dormant --rw-off --rw-on --eq-off-tint`；landing bucket 由 server `LD_BUCKET_COLOR` 供（`--ld-*`）；區色 `--z-za --z-zb --z-zc`＋`--ld-zone-fill`。**現行值逐位元照抄**，§4 C 項矛盾未裁前不統一。
- **頁面本地 palette**（daily／monthly／pm／cost…）沿用原名原值，搬進 `board.css` 的 `[data-page="daily"]` 區塊，不再逐頁 inline。
- **兩組 `--good/--bad` 值**（#1f9d57/#c4504f vs #1e7d4f/#b3423a）：v2 **暫按頁沿用原值**（`[data-page]` 覆寫），並列為 §4 C-5 待 Chris 裁的第一題。（2026-09-28 已裁：v2 light 全站收斂深組，見 C-5）

### §2.3 nav（v2）
- 三組 tab（生產／製程／設備）與現行分組、順序、tier 顯示邏輯一字不差（`_nav.html` 是規格來源，含「⋯」收納組）；差別只在樣式與右上角~~三段切換~~版式開關（09-28 晚 §H-3 起＝一顆二態 `Laptop｜Projector`；v1 nav 同印此開關）。
- 未重做的頁（§0）在 v2 殼下走 `tpl()` 回舊 template ⇒ 它們顯示 v1 nav；這是已知、可接受的過渡（上線前若 Chris 要求一致，再給這幾頁套 `_base` 殼但保留原 body）。

## §3 保留清單（舊頁有的、新頁一個不少；驗收用腳本比對）

> **來源紀律（Chris 2026-09-26 糾正）**：保留清單**由 lead 從專案文件推導**——各頁 SPEC 鐵則／反模式、`_HANDOFF-ARCHIVE.md` 裡的歷次裁決、`FILES.md` 逐檔鐵則、smoke 釘的渲染行為、template 本身的每個元素——**不是拿去問 Chris**。每批動工前先產該批各頁的逐頁清單（附錄 B，逐條附出處），建置派工單與 finish reviewer 都以它對照；Chris 只在最後一頁一頁看、抓漏。下列 1–9 是跨頁通則；**第一批四頁的逐頁清單＋Fable 對未決項的裁決 R1–R9 ＝ `_UI-V2-KEEP-BATCH1.md`**。

1. **內嵌連結**：每個 `<a href>`（機台名→`/machine/{eid}`、站名→`/station/{cls}`、EID、工單號、CSV 匯出、翻譯鈕、「How this board is built」、cmms 連結、`?tag=` 篩選…）。驗收：同頁同請求 v1 vs v2 的 `href` 集合相等（允許 v2 多、不允許少；`?ui=` 例外）。（Chris 2026-09-26 點名）
2. **卷軸窗**：landing 四個並排窗（Capacity／Output／Under repair／Dispatch）保留獨立卷軸與**全量列**，不縮成前 N 列；其他頁的可捲區同理。（Chris 2026-09-26 點名）
3. **tooltip**：所有 `title=`、`?` 說明、截斷文字的完整 `title`（C-LD38）照舊；截斷規則的 class 仍由 smoke 從 `ellipsis` 推導。
4. **互動與狀態**：hover（含空心元素整面 `pointer-events:all`）、`<details>` 摺疊、R/A/M tag 切換、日期／窗選擇器、Wall mode、zoom（landing）、htmx 輪詢、翻譯鈕、CSV。
5. **動態語意**：down 脈動（≥4h 慢脈、≥24h 角標）、生產永不動、`prefers-reduced-motion` 靜態記號。
6. **文字**：所有 `t.*` i18n key 沿用；不新增未翻譯字串；§23 中文 key 不翻。
7. **誠實標示**：source badge（demo／snapshot／live）、ALL SYNTHETIC 橫幅、信心 weak chip、「—」與原因、freeze 狀態標記。
8. **助理與 WebMCP include**、`page_ctx`、`data-*` 供助理讀的屬性。
9. **可及性**：現有 `aria-*`、鍵盤焦點順序；v2 焦點環從 token 畫（`--focus`）。

## §4 色彩語意規則（不可破；來源與踩法見附錄 A，40 條）

**摘要（建置時逐條對）**：一色一義｜停機只有四實心色（紅維修／藍 PM／紫等料／琥珀等外包），禁綠｜CVD 用 ΔE 量不目測｜舊制狀態永不進圖例｜斜紋＝uptime 唯一 class（/equipment 家族與 /downtime）｜uptime 永不塗 cell 本體｜一頁一圖例、sticky｜同名同色跨頁、template 不寫 hex｜稼動頁停機併一紅 Down｜**空心＝無資料，永不紅、永不 0**｜nomes＝空心＋3.6/2.6 虛線｜推定生產＝綠填＋同虛線＋(inferred)｜est 虛線 2.4/1.8 另一 pattern｜dorm＝slate＋◌｜空心整面吃 hover｜無資料座格塗區色調 panel 等效色｜只有 down 脈動｜信心 low 疊 45° 斜紋／weak chip｜運行窗外＝--rw-off 底色非 verdict｜非停機 cell 永不整格填｜不可測一律「—」｜**色永不是唯一通道**｜**文字不塗資料色、KPI 標題中性＋色點**｜/monthly /downtime 禁 Δ 上色｜人不上紅綠｜金錢不色階｜重工只畫良品段內斜紋｜感測器超規同設備紅｜產品標籤 observed 實心／default 空心、無第三形｜dormant 不進停機熱圖｜/daily verdict 住符號｜/saltmarsh/weekly 保 PPTX 色｜24h 軸月份虛線分隔｜主題跟隨系統｜英文預設中文切換｜截斷必掛 title｜面積說話線條閉嘴。

**2.0 額外紀律（來自 EICAS 語法，與上列相容）**：chrome（nav、標題、規線、按鈕）**全部無彩**；資料色只出現在資料標記（機台、bar、lamp、chip）；琥珀＝注意、紅＝警告只用在**狀態**上，永不用在裝飾或強調。

**矛盾待裁（附錄 A §C 12 項）**：建置期**維持各頁現值**；每批驗收時我拿該批涉及的矛盾請 Chris 裁，裁完才統一。第一批會碰到：C-2（Zone A 區色＝PM 藍）、C-5（兩組 good/bad）、C-6（生產綠≠good 綠）、C-7（SPEC-LANDING §4.1 表過時）。

## §5 兩個版式的語法

### §5.1 共通（1.5 與 2.0 都適用）
- **量身打造**：老闆頁（`/`、`/daily`、`/monthly`、`/station`）主數字在投影距離可讀（§6 密度「projector」級）；工程師頁（`/equipment`、`/utilization`、`/availability`、`/machine`）密度優先、可比對。
- **克制**：拿掉後資訊量不變的元素一律不留——裝飾漸層、卡中卡、純裝飾徽章、沒接狀態的動畫、重複圖例、重複標籤（Saville 捐）。
- **主次分明用在區塊層級**：同一區塊內主數字 vs 輔助數字的字級／字重差距 ≥ 2 階；**不做「一頁一主角」的頁面層級要求**（Chris 2026-09-26 明示：資訊平台一頁可多指標多源）。
- **符合預期**：可點的長得像可點（連結有底線或明確 hover；按鈕有邊界與按壓態）；不可點的**不得**用膠囊、不得有 hover 變色。現行「膠囊萬用」廢止：靜態標籤＝純文字或方角小標；R/A/M 等**可切換**的 tag 才是按鈕形。`aria-pressed` 給不改 URL 的切換鈕；會改 URL 的導覽式分段（`.seg a`、v2 nav `.board-seg`）用 `aria-current="true"`（R27）。
- **解釋文字降級**：方法論／免責／口徑說明一律移到區塊底部的小字（`--mut`、比正文小一階）或該頁 notes／`?` tooltip；**不得**與數字同字級並排。內容一字不刪（誠實原則），只換位置與字級。
- **數字**：tabular numerals、右對齊、單位在欄頭不在每格；PH 為單位（memory product-unit-ph）。
- **每個下鑽狀態都是可回上一步的 URL**（HyperCard 捐）；現行 `?focus=`、`?tag=`、`?window=` 沿用。
- **一個控制傳遍全頁**（Miura 捐）：`source`／`lang`／`window`／`tag` 改一次全頁跟著變——現行已如此，v2 不得退化。

### §5.2 1.5（標準看板做到頂）
- 資訊結構＝現行（KPI 列在上、主圖在下、側欄清單）；元件＝卡片格線：1px `--line` 邊、無陰影、無漸層、圓角 ≤ 4px、同一頁所有卡同一種卡。
- 顏色比現行收斂：卡頭中性、數字中性、只有資料標記與 verdict 符號帶色。
- 字級三階（標題／正文／註）＋數字大字一階；4px 間距格。
- 目標讀者：習慣現行版面的人；零重新學習成本。

### §5.3 2.0（警示堆疊＋儀表帶）— direction contract
- **THESIS**：正常的東西不說話；只有異常才有顏色，而且異常排成一疊、嚴重的在最上面。拒絕的類型預設＝「四張等大 KPI 卡壓在主圖上方」。
- **OWN-WORLD**：中性底（light 淺灰面板／dark 深面板）＋墨色正文；零卡片，細規線（1px `--line`，區塊間 `--line2`）分區；固定星座式版面——同一頁每次打開東西在同一個位置；數值一律「值＋趨勢」（真趨勢：Δ 或有歷史序列的 sparkline；**沒有歷史序列就只印 Δ 或不印，永不畫假線**）；chrome 無彩。
- **STORY**：讀者掃圖 → 掃右側那疊 → 需要數字再看下方儀表帶；三秒答「哪台停、停多久、今天損失多少 PH」。
- **FIRST VIEWPORT（`/` landing，1600×1000）**：平面圖佔上半屏（最大元素，左 ~70%），右側固定寬 alert stack（順序：停最久 → PM 逾期 → 派工缺人 → 該跑沒跑；每列＝色塊 lamp＋機台／束＋時長或原因；lamp 帶文字）；圖例一份在圖下；下方一條儀表帶把 Capacity／Output／Under repair／Dispatch 四窗排成一列，**每窗保留獨立卷軸與全量列**（§3-2），窗頭＝主數字＋Δ。
- **FORM**：EICAS/ECAM 機組警示顯示的語法（訊息按嚴重度堆疊、正常無色、值配趨勢、固定版位），我方 grounded 清單第 4 名，seed `e1798e8d`。**只取語法不取服裝**：無邊框儀表、無發光、不強制深色。
- **捐入的紀律**：夜航六儀表→值必配趨勢；Saville 封套→一頁一圖例、不重複標籤、大面留白；TDR→密度分兩級用 container query 宣告（§6）；HyperCard→每個狀態都是網址；Miura→一個控制傳遍全頁。
- **RISK**：紅／琥珀嚴格保留給異常後，畫面會比現行「安靜」很多——這是設計意圖，驗收時要跟 Chris 講清楚，不是掉色。
- **FINISH**：unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

### §5.4 兩版式在各頁的差異表（建置時逐頁填；第一批先填）
| 頁 | 1.5 | 2.0 |
|---|---|---|
| `/` | KPI 四卡在上、圖在下、側欄三小卡 | 圖在上、alert stack 右、儀表帶在下 |
| `/daily` | 現行結構、卡片收斂 | 驗證區（25/42）為主、43 站線圖只亮異常站（正常站無框無色）、解釋文字降級 |
| `/equipment` | 現行結構（已成熟）、膠囊改按鈕／文字 | 同 1.5 ＋ 摘要列改 alert stack 形（down now／quiet／not scheduled 三段） |
| `/station/{cls}` | 現行結構、免責段降級 | hairline 分節＋四個 A/U/Q/OEE 軸儀表帶（`board.css` `/station` 2.0 區塊）、免責段降級；**無 Δ**（需上一窗序列＝38 站再算一遍，§5.3「沒有歷史序列就不畫」）；**不另給專屬形**（09-28 Fable 裁老闆頁 2.0 不開；投影後不夠再開、先交 ASCII） |
| 第二批六頁＋`/yield/station`（09-28） | 移植頁作法：v1 body 逐字移植、CSS 分版式 | **同 1.5、只差殼**（本節未給 2.0 專屬形；第三批前決定要不要補，見 ROADMAP） |

第三批亦採移植頁作法（R23）；老闆頁（`/monthly`、`/station`）2.0 專屬形 09-28 Fable 裁**不開**（KEEP-BATCH3 §H-2）。

## §6 字型與密度

- **一套工作用 sans、自 host**（`static/v2/fonts/`，woff2）：候選 Barlow／Archivo／Source Sans 3／Public Sans（Chris 未綁定；禁 Inter、Space Grotesk、IBM Plex、DM Sans、Outfit、Plus Jakarta 等訓練集預設臉）。要求：tabular figures、字重 400/600 兩階即可、授權 OFL。**中文 fallback**：`"Noto Sans TC", "Microsoft JhengHei", system-ui` 接在後面；標點與數字混排要驗。
- **密度兩級**（TDR 捐）：`data-density="laptop|projector"`，projector＝字級 ×1.25、列高 ×1.2、主數字 ×1.4；由 nav 切換（記 cookie `board_density`），預設 laptop；老闆頁投影時切 projector。**2026-09-28 Chris 裁：獨立密度切換併入版式開關**——~~`1.5`＝laptop、`2.0`＝projector、`1.0` 不動密度~~ ⇒ 09-28 晚 §H-3 起開關為二態場景詞：`Laptop`＝`?ui=15&density=laptop`、`Projector`＝`?ui=2&density=projector`（`?ui=v1` 不上 nav、不動密度）；`?density=` 參數與 `board_density` cookie 保留、無 UI（直打 `ui=15&density=projector`／`ui=2&density=laptop` 組合仍可、刻意無入口）。用 CSS 變數實作，不用 JS 重排。
- **字級**：固定階、不 clamp（建成物用 `px × --k`，非 rem——2026-09-27 documenter 核對後改記為 px，語意同「固定階」）：正文 14／標題 15／小字 12.5／註 12／標籤 11／主數字 30／次數字 22，projector 乘 1.25（主數字 1.4）。移植頁沿 v1 尺寸（daily hero 58、equipment／station 34）＝現值，未統一。行長：prose ≤ 75ch；表不限。
- **對比**：正文 ≥ 4.5:1、大字 ≥ 3:1（雙主題各量）。

## §7 派工與工作形（模型分派規則 2026-09-24）

- **Fable**：本檔、每批派工單、審 diff、親看截圖（雙主題 × 雙語 × 兩版式 × 兩密度，至少 light+en 全看、其餘抽看）、commit。
- **opus（worktree）**：所有碼；每批一個 worktree、一個 agent；同時最多 1–2 個寫碼 agent（memory agent-dispatch-pacing）。
- **impeccable**：每批完成後跑 `impeccable detect --json <changed>` 一次（無 hook）；再 spawn `impeccable-finish-reviewer`（原始需求、§5 契約、截圖路徑、detect findings、`craft-floor.md`）；disposition 四選一照 skill；第一批結束 spawn `impeccable-documenter` 產 `DESIGN.md`＋`.impeccable/design.json`（從建成物寫，不從意圖寫）。
- **codex**：不派（跨檔判讀重、需要跑 smoke 自驗）。
- **派工紀錄**（四欄 lane／effort／一次過或退件／退件原因）記在 §11。

## §8 批次

| 批 | 頁 | 附帶 |
|---|---|---|
| **1** | 共用殼＋token＋元件＋切換機制；`/`（landing2）、`/daily`、`/equipment`、`/station/{cls}`（含 `/station` 索引） | smoke 新組 `ui_v2_*`；DESIGN.md 初版 |
| **2** | `/monthly`、`/availability`、`/utilization`（index.html）、`/equipment/downtime/monthly`、`/equipment/pm`、`/output`＋`/yield` | 矛盾 C-1（斜紋多義）、C-3、C-4 已裁（09-28，Fable）。**狀態（09-28）：建成、已進主線（`3adb7b3`→`94b1f3a`），待 Chris 看**；六頁＋YS 均未給 2.0 專屬形（沿第一批「移植頁」作法：同 body、CSS 分版式），第三批前決定要不要給 |
| **3** | `/machine/{eid}`、`/equipment/health`、`/manpower`、`/dispatch`、`/rework`、`/budget`、`/cost*`（三頁） | 無 demo 形的頁在有 store 的機器驗。保留清單＝`_UI-V2-KEEP-BATCH3.md`（§E 20 題已裁 R23–R42，見其 §H；分包 §F 包 0／A／B／C1／C2）。**✅ 2026-09-28 建成（包 0／A／B／C1／C2＋三修正批；KEEP-BATCH3 §H）**：主線 `1b0bf15`→`8819bd9`，待 Chris 看 |
| — | `/saltmarsh/weekly`、`/landing/v1`、`/updown`、`/assistant` | 不重做（§0） |

每批＝一個 worktree、一個 opus agent、一張派工單（引用本檔章節不抄）；批內先建殼與元件、再逐頁；每頁完成即跑該頁 smoke 與 `detect`。

## §9 驗收（每批、每頁）

① **口徑等價**：同一請求（同 source／lang／window）v1 與 v2 的頭數字、狀態計數、「—」位置逐位元相同——寫成 smoke `ui_v2_parity_<page>`（抽 v1 渲染的關鍵數字字串，斷言 v2 渲染含同值）。
② **連結等價**：`href` 集合 v2 ⊇ v1（去 `?ui=`）——smoke `ui_v2_links_<page>`。
③ **色彩規則**：`templates/v2/**` 零 hex；狀態色只經 `var(--st-*|--ld-*|--z-*)`；停機圖例四色＋條件出現；空心規則；斜紋只在允許頁——smoke `ui_v2_color_*`。
④ **雙主題雙語**：demo 站截圖 light/dark × en/zh × 15/2 × laptop/projector；Fable 親看 light+en 全部、其餘抽看；截圖存 `.impeccable/review/`。
⑤ **detect**＋**finish reviewer** disposition ＝ ship 或 fix 已清。
⑥ **舊 smoke 全綠**（主線 3775 基線；worktree 少的 snapshot 組 SKIP 非回歸）。
⑦ **助理**：v2 下問 3 題（Q10／Q7／Q8）答案與 v1 相同。
⑨ **分支等價**：v2 body 逐字移植舊 template 的 Jinja 條件式；smoke `ui_v2_branches_<page>` 斷言 v2 `{% if %}` 條件字串集合 ⊇ v1（只在特定狀態出現的揭露列／chip 靠這條機械保證，見 KEEP R3）。
⑧ **Chris 親看**：demo 站 `?ui=2` 與 `?ui=15`；他說可以才進下一批；**全部完成後由 Chris 決定是否上線與預設值**（2026-09-28 已裁：上線、預設 1.5，§1.3）。

## §10 反模式（本輪新增；舊 SPEC 各頁反模式照舊有效）

1. 為了「安靜」把資料色也拿掉（B 出局的原因）——安靜的是 chrome，不是資料。
2. 假趨勢線（無歷史序列卻畫 sparkline）。
3. 縮短卷軸窗、砍列數來換版面整齊。
4. 把解釋文字刪掉而不是降級。
5. 兩版式各寫一套 template。
6. 在 v2 template 寫 hex、或發明第二個停機 token。
7. 為了統一而擅自解 §4 附錄 C 的矛盾。
8. 改舊 template「順手修一下」。
9. 用 CDN 字型或 CDN 圖表庫。
10. 膠囊當萬用容器。

## §11 派工紀錄

| 日期 | 包 | lane／effort | 結果 | 退件原因 |
|---|---|---|---|---|
| 09-26 | 頁面盤點 | opus／default | 一次過 | — |
| 09-26 | 色彩規則盤點 | opus／default | 一次過 | — |
| 09-26 | 三向 mock（scratchpad） | opus／default | 一次過（B 因規則出局屬設計判定非退件） | — |
| 09-26 | 批次 1a：殼＋切換＋元件＋landing 兩版式（wt `agent-aa832bd6…`，5 commit） | opus／default | 一次過建置；finish reviewer 判 **fix**（8 項：2.0 右欄裁切藏 Event feed／派工燈文字塗色／`--unk` 對比／Why 欄換行／投影 nav 溢位／兩種紅→C-14／unicode 圖示／nav 重複標籤）→ 修正批派回同一 agent | 反模式非口徑 |
| 09-26 | finish reviewer（landing） | impeccable-finish-reviewer／default | 一次過（五節齊、disposition fix） | — |
| 09-26 | landing 修正批 1（8 項）＋批次 1b（/daily、/equipment 家族、/station；wt 同上，6 commit） | opus／default | 建置一次過；landing verdict pass＝5 resolved／2 partial（右欄摺線下群組未宣告、Why 欄殘幹）／1 記錄；1b reviewer 判 **fix** 8 項（daily 投影 hero 斷行、station 數值塗色→C-15、equipment 堆疊孤兒「·」、down-now 鈕紅框、station ⓘ glyph、token 對比→C-16、「顆」→ROADMAP、/station 索引未截圖）→ 合併修正批 2 派回同一 agent | 反模式非口徑 |
| 09-26 | finish reviewer（1b 三頁，新 reviewer） | impeccable-finish-reviewer／default | 一次過（五節齊、disposition fix） | — |
| 09-27 | 修正批 2（L1／L2／D1／E1／E2／S1／S2／S3；`aeac4f2`）＋派工窗微修（`c708e3c`） | opus／default | verdict pass：1b 三頁 **ship**；landing L1 resolved、L2 partial（欄寬換行）→ 裁 Why 改整列第二行（純 CSS）再修 | 欄寬取捨 |
| 09-27 | documenter（DESIGN.md＋design.json，worktree） | impeccable-documenter／default | 一次過；附移植頁紀律殘留清單 → 清理批 3 派回建置者 | — |
| 09-26 | 逐頁保留清單推導（landing+daily／equipment+station） | opus／default ×2 | 一次過（各 ~2k 字、逐條有出處；§E 未決 9 題由 Fable 裁＝KEEP R1–R9） | — |
| 09-28 | 色彩三題落地 | codex gpt-5.6-terra high | 一次過（四檔 44+/34−；smoke 4252/4252；Fable 親看四頁 light） | — |
| 09-28 | 第二批六頁保留清單推導（`_UI-V2-KEEP-BATCH2.md`） | opus／default ×1 | 一次過（六頁各 1.2–1.9k 字、§E 27 題由 Fable 裁＝R10–R22、§F C-1/C-3/C-4 已裁、§G 10 條進 ROADMAP） | — |
| 09-28 | 第二批 §E 抄題／覆蓋判讀 | opus／default | 一次過 | — |
| 09-28 | 裁決寫回（R10–R22 → KEEP-BATCH2 §H、附錄 C-1/C-3/C-4、§8／§11、ROADMAP） | opus／default | 一次過 | — |
| 09-28 | translate (k2) 跨午夜 flake 診斷 | opus／default | 一次過（`llm_budget_day()` 每次重讀 now；單跑 16/16；修法記 ROADMAP） | — |
| 09-28 | 批次 2 建置：六頁＋`/yield/station` 兩版式（wt `agent-a88c50d2…`，9 commit；主線 `3adb7b3`→`f398756`） | opus／default | 建置一次過（`templates/v2` +14 檔、board.css +770、`app.py` 13 行 `tpl()`、smoke +837 條、v1 零觸碰）；finish reviewer 判 **fix** 5 項（有色側條／RAW 標填底／2.0 月報白卡／PM 條內字對比／`?` vs `i`）→ Fable 裁 1–4 修、5 記 ROADMAP | 反模式非口徑 |
| 09-28 | finish reviewer（批 2 七頁） | impeccable-finish-reviewer／default | 一次過（disposition fix） | — |
| 09-28 | 批 2 修正批（1–4；`94b1f3a`，wt 原 `e96a91b`） | codex gpt-5.6-terra high | 一次過（純 CSS；Fable 再看月報／PM 截圖）；worktree smoke 4964/4976（12 環境）、主線 5089/5089 | — |
| 09-28 | documenter（DESIGN.md＋design.json 更新至批 2；`a2c9c28`） | impeccable-documenter／default | 一次過（寫在 worktree、lead 複製進主線；DESIGN 351→497 行、色 token 41→80、元件 13→26、Edge-Not-Fill；附紀律殘留 9 條 → ROADMAP） | — |
| 09-28 | 收工五十二專案 md 更新 | opus／default | — | — |
| 09-28 | C-17 落地（R11 維持／R22 達標條中性；`1b0bf15`） | codex gpt-5.6-terra high | 一次過（順手改 daily 移植塊三行、Fable 還原） | — |
| 09-28 | 第三批保留清單（`_UI-V2-KEEP-BATCH3.md`，1147 行；`ceb59c1`） | opus／default | 一次過（⚠ 自開六個子 agent，違派工節奏；下次派工單明寫禁止） | — |
| 09-28 | 裁決寫回（R23–R42 → KEEP-BATCH3 §H 等） | opus／default | 一次過 | — |
| 09-28 | 批 3 包 0（`.help` 畫 `i`、C-16 全清、`/monthly` 選中態；`9271535`） | codex gpt-5.6-terra high | 一次過 | — |
| 09-28 | 批 3 包 A（`/machine`＋`_timeline`＋`/equipment/health`；wt `agent-a40194d1…`） | opus／default | 一次過；reviewer 退 4 必修＋2 順手 | 反模式非口徑 |
| 09-28 | 批 3 包 B（`/dispatch`＋`/manpower`；wt `agent-ae8d1382…`） | opus／default | 一次過；reviewer 退 2 必修＋3 順手（R30 綑帶漏洞＝裁決的鍋） | 反模式非口徑 |
| 09-28 | A＋B 修正批（`6c83dd8`） | codex gpt-5.6-terra high | 一次過（U+2B07 註解 1 條，Fable 改註解） | — |
| 09-28 | 批 3 包 C1（`/cost*`；wt `agent-adb9e22c…`） | opus／default | 一次過；reviewer 退 1 必修＋1 順手 | 反模式非口徑 |
| 09-28 | 批 3 包 C2（`/budget`＋`/rework`；wt `agent-a96d05ab…`） | opus／default | 一次過；reviewer 退 2 必修＋3 順手；⚠ 一次 `source=live` TestClient 查詢 | 反模式非口徑 |
| 09-28 | C 組修正批（`8819bd9`） | codex gpt-5.6-terra high | 一次過（parity 實體數字＝夾具問題，Fable 補 `_uiv2_text`） | — |
| 09-28 | finish reviewer ×4（批 3 A／B／C1／C2） | impeccable-finish-reviewer／default | 一次過（皆無 Chrome MCP、source-only） | — |
| 09-28 | 收工五十三專案 md 更新 | opus／default | — | — |

**測試基線（09-28 批 3 完）**：主線 `run_smoke` **6457/6457 PASS**（新增 `ui_v2_machine/health/dispatch/manpower/cost/budget/rework`）；worktree 預期 12 環境 FAIL＋1 SKIP；`ui_v2_parity_equipment` days=7 曾一次分鐘邊界 flake（重跑即綠）。

---

## 附錄 A：色彩語意規則盤點全文（opus 2026-09-26；原檔 `scratchpad/redesign/color-rulebook.md`）

（全文一字未改搬入；§4 摘要引用此處編號）

### A) Token 表（light → dark；"=" 表示 dark 未覆寫）

**基底 token**（幾乎每個 template 相同）。定義 `templates/equipment.html:29-31`、dark `:89-91`；`landing2.html:18-20/30-32`、`index.html:12-14/25-27`。

| Token | Light | Dark | 意義 |
|---|---|---|---|
| --bg / --panel / --panel2 / --line | #f4f6fb / #ffffff / #eef2f8 / #d8e0ec | #0b1220 / #141d2e / #1b2740 / #2a3852 | surfaces |
| --track / --fg / --mut / --hover | #e6ebf3 / #1b2433 / #5c6b82 / #b9c6db | #0e1626 / #e6edf6 / #8aa0bd / #3a5c86 | track, text, muted, hover |
| --good / --warn / --bad | #1e7d4f / #b07d12 / #b3423a | #36c47f / #cbb26b / #e0726f | 好／注意／壞 verdict（09-28 收斂；舊值 #1f9d57/#c4504f 存證見 C-5） |
| --unk | #b07d12 | #cbb26b | unknown/demo/draft（同 --warn） |
| --ptag / --ptag-ink | #536f99 / #fff | #9bb7de / #0b1220 | R/A/M product-tag chip |

⚠ `monthly`/`manpower`/`cost`/`budget`/`rework`/`downtime` 的 light --good/--bad 是 **#1e7d4f / #b3423a**（`monthly.html:23`）。

**停機狀態**（/equipment, /equipment_health, /equipment_pm）。`equipment.html:56-64`、dark `:98`；CVD 量測紀錄在 :34-55。

| Token | Light | Dark | 意義 |
|---|---|---|---|
| --st-repair | #dc2626 | = | 維修 紅 |
| --st-pm | #2563eb | = | 計畫保養 藍 |
| --st-parts | #6b21a8 | #b26ef5 | 等料(停機) 紫 |
| --st-vendor | #d97706 | = | 等外包(停機) 琥珀 |
| --st-legacy（別名 --st-setup, --st-maint） | #9ca3af | = | eMaint 舊制（調機／其他維護）灰 |
| --st-hwait（別名 --st-htest） | #8b98a9 | = | uptime hold 不計停機；永遠配 --tex-stripe |
| --tex-stripe | rgba(0,0,0,.30) | = | 唯一的 uptime 斜紋 |
| --st-dormant | #64748b | #94a3b8 | 長停 slate ＋ ◌（:65-72） |
| --rw-off / --rw-on | #cbd5e1 / #eef4fb | #0a101c / #2a3b5c | 排定運行窗外／內；只是底色非 verdict（:76-81）；index/machine/availability 必逐位元相同 |
| --eq-off-tint | rgba(15,23,42,.045) | rgba(255,255,255,.06) | 非工作日 tint |

**Server 端 canonical 色** `app.py:235-243` `STATE_COLORS`（無 dark 變體）：
生產 #16a34a｜計畫保養 #2563eb｜流程等待 #0891b2｜待機 #14b8a6｜該跑沒跑 #eab308｜非排定 #475569｜維修＝故障停機 #dc2626（刻意共用 :232-234）｜不明 #9ca3af｜等料(停機) #6b21a8｜等外包(停機) #d97706｜調機／其他維護／W8／舊制工單 #9ca3af｜未拆分 #94a3b8。未知 fallback #9ca3af。`RW_DOWN_COLOR`＝維修紅（:258）；`AV_UP_COLOR`＝`var(--av-up)`（:273）。

**Landing 機台 bucket** `app.py:16105-16116` `LD_BUCKET_COLOR`：run #16a34a｜down #dc2626｜idle #eab308｜wait #94a3b8｜dorm #64748b｜nodata None（hollow）｜nomes None（hollow, dashed）。`landing2.html:23` 的 `--ld-down` 由 server 供、template 不得硬編。

**區色** `landing2.html:155-156`（fill opacity .10 light / .18 dark）：Zone A #2563eb/#60a5fa｜Zone B #7c3aed/#a78bfa｜Zone C #0d9488/#2dd4bf。--seat-zone 預設 --mut、逐機取區色（:318-320）。/downtime 另有 --dt-za/--dt-zb/--dt-zc slate #1e293b/#64748b/#cbd5e1，dark #64748b/#94a3b8/#e2e8f0（`downtime.html:23,31`）。

**頁面本地 palette**

| 頁 | Tokens | 出處 |
|---|---|---|
| /daily | --b-prod #1f9d57, --b-wo #dc2626→#e0726f, --b-starved #6b21a8→#b26ef5, --b-blocked #2563eb→#6ea8ff, --b-unexp #8b98a9, --bar-neutral #7d8ca1 | `daily.html:29-46` |
| /daily loss chips | --p-scrap #be185d→#db2777, --p-rework #b07d12→#d97706, --p-slow #0d9488 | SPEC-DAILY-UNIT §7 |
| /availability | --av-up #6fae9c→#2f6b59（該頁唯一新 token） | `availability.html:28,37` |
| /monthly | --l-down #dc2626, --l-yield #b07d12, --l-rework #2563eb（dark #e0726f/#cbb26b/#5b8def）；--acc-n #2563eb；--gapfg | `monthly.html:24-35` |
| /equipment_pm | --pm-age1..4（#8b98a9→#b07d12→#c4803f→#c4504f）；--pm-area-*；--pm-bar/bar2 一色兩階；--pm-line1 #2f5597 實線／--pm-line2 #9c5fb0 虛線 | `equipment_pm.html:50-85` |
| /dispatch | --lamp-need-bg #c4504f, --lamp-down-fg #a13b3a, --lamp-lend-fg #177a43；--st-legacy dark #8b98a9 | `dispatch.html:17-32` |
| /cost | --b-one #2563eb, --b-part #1e7d4f, --b-unk #8a94a6（刻意中性） | `cost.html:28-40` |
| /budget | --acc-a 綠(approved), --acc-p 琥珀(pending) | `budget.html:20-29` |
| /manpower | --acc-n #2563eb 正常班, --acc-o #0d9488 計畫外；--hatch；--band0-3 | `manpower.html` |
| /rework | --rw-seq 單色橘 sequential | `rework.html:19` |
| /saltmarsh/weekly | --cw-* PPTX 色，只調明度／飽和 | `saltmarsh_weekly.html:50-61` |

### B) 規則清單（規則 — 出處 — 重設計怎樣會踩）

1. 一色一義；區色不進狀態色家族；地圖上的區標＝區圖例。— SPEC-LANDING §18.10-d:1839-1847；MEM visual-spec-measure-and-look:18。踩：圖例重加區色塊、Zone B 用綠。
2. 停機只有四實心色：紅維修／藍 PM／紫等料／琥珀等外包。— MEM equipment-page-encoding-rules §1；`equipment.html:39`。踩：第五停機色、停機上紋理。
3. 停機狀態禁綠（讀成 pass）。— 同上；`equipment.html:55`。
4. CVD 禁令要量不能目測：PM 藍旁禁中紫（#7c3aed↔#2563eb ΔE 1.7）；#e11d48 禁（tritan ΔE 3.6 對維修紅）；黃／橄欖禁（對琥珀 ΔE 1.5–7.9）；紫在 light 要比 PM 藍深、dark 要比它淺；雙主題重跑 validator 並親讀 tritan 數字（PASS 閘漏 tritan）。— `equipment.html:46-54`。
5. 舊制狀態（調機／其他維護／舊制工單）永不進圖例、不配色；真出現就 #9ca3af＋tooltip 名。— `app.py:226-231`。
6. 斜紋＝uptime（不計停機），唯一一個 class；圓點紋已退役；子型只在文字裡叫名。— `equipment.html:41-45`。（09-28 C-1 裁：專屬性只在 /equipment 家族與 /equipment/downtime/monthly 強制；他頁斜紋＝不知道一族）
7. uptime 永不塗 cell 本體；只出現在 cell 下 4px 組成條；uptime-only 日＝透明虛線 cell（`.eq-uptonly`）。— `equipment.html:359`。
8. 圖例出現規則：uptime 圖例永遠顯示；等料／等外包／舊制只在窗內發生時出現；/availability 永遠四停機＋一舊制灰（`app.py:282-284`）。
9. /equipment 圖例 sticky 捲動可見。— `equipment.html:402-407`。
10. 一頁一圖例，機隊／母列／子列共用。— SPEC-RUNWINDOW §6.5 ⑤:1060；SPEC-AVAILABILITY C-AV9。
11. 同名同色跨頁；永不發明第二個停機 token；template 引用變數不寫 hex；兩張 STC map 逐位元相同。— `app.py:251`；SPEC-AVAILABILITY C-AV10:286,311；`landing2.html:21-22`。
12. 稼動頁（/utilization, /machine）全部停機併成一個紅 Down；tooltip 保留原工單類別；`RW_DOWN_STATES` 是白名單。— `app.py:244-265`；SPEC-RUNWINDOW §6.6。踩：把不明／待機塗成 down。
13. 不知道≠壞（C-LD2）：no data＝空心框，永不紅、永不 0。— SPEC-LANDING §11:400；`app.py:5451`。
14. 不可測（nomes）＝空心＋3.6/2.6 虛線，獨立計數、無新色；與 nodata（純空心）不同。— SPEC-LANDING §4.1:118-130、C-LD41:424；`landing2.html:250`。踩：nomes 填灰（讀成永遠 idle）。
15. 推定生產＝綠填＋同 3.6/2.6 虛線＋「(inferred)」標。— §18.26:2858；`landing2.html:257`。
16. 尺寸估計虛線是另一個 pattern（2.4/1.8），不得與 nomes 虛線合併。— `FILES.md:5787`；`landing2.html:242`。
17. 長停（dorm）獨立 bucket，slate-500＋◌；永不併進 wait；◌ 是主要區分。— C-LD45 SPEC-LANDING:3084；`app.py:16101-16113`。
18. 空心 cell／block 整面要吃 hover（`pointer-events:all`，非透明填）。— §18.27；`landing2.html:225-237`。
19. 無資料座格塗「區色調的 panel 等效不透明色」（非白非透明）。— CLAUDE.md §18.40。
20. 動態承載嚴重度：只有 down 機台脈動（≥4h 慢脈、≥24h 加角標）；生產永不動；reduced-motion 下脈動變靜態記號。— SPEC-LANDING §4.1:131；`landing2.html:352-358`。
21. 信心 enum high/medium/low（C3；`CONCEPT.md:4463`）；index／timeline 的 low 段疊 45° 斜紋（`index.html:94`, `_timeline.html:17`）；landing 顯 --unk「weak」chip（`landing2.html:384`）；標籤走 CONF_LABELS（`app.py:309`）。
22. 排定運行窗外＝最深中性灰（--rw-off）；日曆未涵蓋＝panel 色＋45° 細斜紋（`repeating-linear-gradient(45deg, var(--line) 0 1px, transparent 1px 3px)`；2026-10-02 自「點紋」更正，見 C-1／KEEP-BATCH2 §G-1），永不與 --rw-off 同；皆為底色非 verdict。— SPEC-RUNWINDOW §6.2 ③:601、反模式 2:636；`equipment.html:108-118`；實作 `index.html:101-102`、`availability.html:86-87`、`_timeline.html`（`.tl-bar.rw .rwunc`）。
23. 非停機 cell 永不整格狀態色填；窗內零小時顯淡「—」（永不 0.0）；停機全在窗外顯虛線「0.0」。— `equipment.html:126-131`（C-RW10）。
24. 不可測一律「—」，永不 0 或 100%；需要時一行原因。— C-LD12；`monthly.html:13`（C-MR3）；tag 過濾下 OEE 顯「—」＋tooltip；名冊列缺資料仍顯「—」。
25. 色永不是唯一通道：燈號帶文字、chip 帶單位＋符號、bar 帶數字。— SPEC-DISPATCH:38；SPEC-DAILY-UNIT §7:398；SPEC-Saltmarsh Assembly-WEEKLY:107；SPEC-DISPATCH-V2 C-KD14。
26. 文字不塗資料色；KPI 標題中性＋色點。— MEM board-dashboard-ux-feedback:40。
27. /monthly 與 /downtime 禁 Δ 上色（聖誕樹）。— SPEC-MONTHLY A2:331,465；SPEC-DOWNTIME:389。（/equipment 的 Δpp 有上色，見 C4。）
28. 人不上紅綠 verdict 色；無排名徽章；完成率中性；金額一色兩階。— SPEC-PM-BOARD §1-3:46, :639-640, :562；manpower 單色 sequential 無紅綠（SPEC-MANPOWER:128）。
29. 金錢永不色階；缺／未涵蓋用紋理（點或斜紋）不用色相。— SPEC-RUN-COST:895-899。
30. 重工只畫成良品段內的斜紋，永不獨立一層。— C-CP5 SPEC-CAPACITY:796。
31. 感測器超規用與設備故障同一紅 #dc2626；正常中性灰；alert 琥珀；offline 空心灰＋斜線。— SPEC-ENV-SENSOR:91,137-138。
32. 產品標籤：observed 實心 --ptag、default／idle 空心；永不加第三形（虛線）；不用灰 placeholder。— SPEC-PRODUCT-TAGS:332-334, :946；`board_tagui.py:22-25`；`equipment.html:151-155`。
33. Dormant 中性 slate 非 good/warn/bad，永不進停機熱圖。— `equipment.html:65-71`。
34. /daily verdict 色收斂：verdict 住 ✓/✕/∅ 符號；bullet 中性；該頁琥珀只代表 rework bucket。— SPEC-DAILY-UNIT Part C:569,588。
35. /saltmarsh/weekly 保 PPTX 色相，只調明度／飽和。— C-W3 SPEC-Saltmarsh Assembly-WEEKLY:36。
36. 24h 軸月份用虛線分隔＋註記區分，不用斜紋或透明度。— SPEC-DOWNTIME:366-367,470。
37. 主題跟隨系統（prefers-color-scheme，雙 palette 完整）。— MEM frontend-conventions:13。
38. 英文預設＋中文切換；語言隨 hx-get 走；canonical 中文狀態 key 不翻、只翻顯示。— frontend-conventions:14,22；`app.py:288-307`。
39. 任何會被 CSS 截斷的文字必掛完整 `title`（C-LD38；smoke 從 `ellipsis` 規則推導 class）。— `landing2.html:90-96`。
40. 面積說話線條閉嘴：區是填色；cluster 是直角虛線框；凸包／bbox 框永不回來（C-LD25）。— MEM visual-spec:16。

### C) 來源互相矛盾／模糊（重設計時要裁；建置期維持各頁現值）

1. 斜紋多義：規則 6 與 SPEC-DOWNTIME:367 說斜紋專屬 uptime；但 low confidence（index/_timeline）、良品段內重工（CAPACITY）、未涵蓋月（RUN-COST:816, FILES.md:756）、/manpower 缺格（SPEC-MANPOWER:129）也用斜紋。專屬性似乎只在 /equipment 家族與 /downtime 強制。 **Fable 裁 2026-09-28**：採 (a)。規則 6「斜紋專屬 uptime」**範圍限 `/equipment` 家族（含 cell drill、health）與 `/equipment/downtime/monthly`**；其餘頁斜紋＝「無資料／量不到／信心低」一族（語意同為「不知道」），寫成規則 6 範圍註。本批六頁沒有同頁一紋兩義，v2 照 v1 各頁現值、不改紋理。`/utilization` 同帶兩種 45° 紋（low 粗紋、未涵蓋細紋）圖例只列後者＝既有狀況；補 low 圖例項會破 RS :9983（下鑽圖例須與頁圖例逐項相同）⇒ **第二批不補，記 ROADMAP 第三批與 `_timeline` 一起補**。文件「點紋」→「細斜紋」記 ROADMAP 修文（§G-1）。結案。**smoke 前提更正（2026-09-28 R38）**：補 `/utilization` low 圖例項會破的是 RS :29497（`ui_v2_utilization` 的 `leg(v1)==leg(v2)`），不是 RS :9983（後者只檢查六個固定字串是否被包含）。**已補（2026-09-28 R38，`2d9739b`；KEEP-BATCH3 §H-2）**：查實 low 粗紋被 segment inline `background:` 簡寫蓋掉、從未畫出 ⇒ 先改 `background-color:` 讓粗紋畫出，再於 `rwleg`／`tl-legend` 兩版末尾補 `legend_low`（低信心（斜紋））與 `legend_off`（未排生產時段（淡色））兩項，v1／v2 同改；v2 dark 加 `--low-stripe: rgba(255,255,255,.22)`（v1 dark 仍不可見＝純視覺、不投資）。
2. Zone A 區色 #2563eb＝PM 藍；Zone B #7c3aed＝PM 藍旁被禁的紫。landing 安全只因 PM 已併進 down 紅；任何同時顯示區與 PM 的頁會撞。
3. 藍／琥珀在他頁另有意義：/daily --b-blocked＝PM 藍；--b-starved＝等料紫；/monthly --l-rework #2563eb 與六態 bar 的 PM 藍同頁（`monthly.html:10-12` 說「兩系統按欄不混」）；--l-yield＝琥珀＝--warn。 **Fable 裁 2026-09-28**：`/monthly` **維持現值**。三損失色與六態組成條分欄不混是 v1 明寫設計（M:10-12），三損失色皆帶文字標籤；範圍註＝「同值兩義**跨欄可容、同欄不可**」。不採 slate 三階（失去「紅＝停了」直覺）。`/equipment/pm`、`/output`、`/yield` 不動。結案。
4. Δ 上色：/equipment 給 Δpp 上 good/bad（`_equipment_body.html:504`），/monthly、/downtime 禁。 **Fable 裁 2026-09-28**：見 R11。`/monthly`、`/downtime` 禁；`/equipment` Δpp 與 `/output` 距目標保留 verdict、但依規則 26 住符號／色點不住數字。本批結案；「DEMO 目標上下 verdict」→ C-17 已裁。
5. 兩組 --good/--bad：#1f9d57/#c4504f vs #1e7d4f/#b3423a，皆 light。**Fable 裁 2026-09-28**：v2 light 的 `--good/--bad` 全站收斂到深組 `#1e7d4f/#b3423a`（在 `--panel/--bg/--panel2` 上分別 5.12/4.74/4.56 與 5.59/5.17/4.98，全 ≥4.5:1）；dark 不動（`#36c47f/#e0726f` 已 7.52/5.47）。語意不變、只換明度；R8「OEE 紅綠維持現值」指 verdict 邏輯與門檻、不指 hex，/daily 一併收斂。第二、三批六頁（monthly/manpower/cost/budget/rework/downtime）本來就用深組，搬進 v2 零換色。C-5 結案。
6. 生產綠≠good 綠：STATE_COLORS 生產 #16a34a vs --good #1f9d57；server 態色無 dark 變體，而 /daily 的 --b-wo dark 會切 #e0726f。**Fable 裁 2026-09-28**：不統一。生產綠 `#16a34a`＝§23 狀態色、`--good`＝verdict 色，語意不同、本就不該同值。結案。
7. SPEC-LANDING §4.1 表（:116）過時：仍列非排定於 wait（已被 dorm C-LD45 取代）；命名 `--st-run/--st-down/--st-idle-bad/--st-wait` 不存在於 landing2.html（用 `--ld-down`＋server hex）。`landing2.html:151` 註解說待機＝#94a3b8，STATE_COLORS 待機＝#14b8a6。
8. Zone C 區 teal #0d9488 與待機 #14b8a6、流程等待 #0891b2 接近；同頁同顯會撞（index.html 組成？未查）。
9. SPEC-PRODUCT-TAGS 自相矛盾：C-PT3（:755）說 default 與 observed「相同」，:948 說 observed 實心／default 空心。碼跟後者。
10. --st-legacy 在 /equipment dark 未覆寫（#9ca3af），/dispatch dark 是 #8b98a9。
11. CONCEPT.md §23（4786-4823）定義 10 態、來源、信心，**無色**；色只住 app.py STATE_COLORS。GOVERNANCE.md 零色規則。
12. Validator 位置：SPEC-DOWNTIME:370 說 `validate_palette.js` 不在 repo（隨 dataviz skill），`equipment.html:52` 引 `scripts/validate_palette.js`。未驗。
13. （2026-09-26 KEEP R5 新增）station 的 Q 值與不良 bar 用 `#d97706`＝`--st-vendor` 同值不同義（品質軸 vs 等外包停機）。v2 給頁本地 token `--cap-q` 值不變；是否換色待 Chris 裁。
14. （2026-09-26 finish reviewer 新增）landing 2.0 警示堆疊同一疊裡兩種紅：down lamp＝server `#dc2626`（維修紅／landing「所有停機一紅」）vs 派工「需支援」lamp＝`--bad`（v1 燈底 `--lamp-need-bg #c4504f`〔`dispatch.html:24`〕；v2 light `--bad` 依 C-5 已收斂為 `#b3423a`，2026-10-02 更正此處舊值）；兩者各被跨頁規則凍結。過渡靠 lamp 文字區分；是否統一待 Chris 裁。**Fable 裁 2026-09-28**：不統一。停機紅 `#dc2626`＝§23 canonical、規則 2／11／12 凍結、smoke 釘 server 值；需支援 `--bad`＝/dispatch 凍結值。兩者皆「紅＝要處理」、lamp 永遠帶字（規則 25），不構成一色兩義；統一任一邊都破另一頁的同名同色。結案。
15. （2026-09-27 finish reviewer 1b 新增）/station 的 A／U／Q／OEE **數值本身**在 v1 塗狀態色／verdict 色（`_station_overview.html:26,28,32`），撞規則 26「文字不塗資料色、KPI 標題中性＋色點」與 §5.2「數字中性」；R8 只豁免 /daily。**v2 裁：色移到標籤旁 8–9px 色點、數值 `--fg`（兩版式）**；Chris 若要保留 v1 塗法，翻這條即可。**Fable 裁 2026-09-28**：維持 v2（標籤旁色點＋墨色數字，兩版式）。規則 26 在 §4 不可破清單、R8 只豁免 /daily；v1 的 U 軸 teal `#14b8a6` 在 `--panel` 上僅 2.49:1、連大字 3:1 門檻都不及格，翻回等於把不及格帶回來；色點照樣承載 OEE verdict（規則 25）。v1 零觸碰。結案。
16. （2026-09-27 finish reviewer 1b 新增）小字資料色對比不足：`--good #1f9d57` 在 `--panel` 上 3.49:1（/equipment Δpp 11px）、`--unk #b07d12` 3.23:1（全合成橫幅粗體 12.5px）。§2.2 凍結 token 值 ⇒ v2 不動；與 C-5 一起裁（另一組 good `#1e7d4f` 為 5.12:1（`--panel`）／4.74（`--bg`））。**Fable 裁 2026-09-28**：`--warn/--unk #b07d12` 值不動（3.63:1；誠實標示色、與 v1 同值）；但 landing 全合成橫幅粗體字 `.srcbanner b` 塗 `--unk` 本身撞規則 26 ⇒ 改 `--fg`，琥珀留在非文字通道。C-16 剩餘（其他小字塗 `--warn/--unk` 處）列 ROADMAP 不在本包。結案。
17. **C-17「達標條與 DEMO 基準 verdict」**

   **R11（`/output` 距目標 verdict）— 維持、不中性化。**
   理由：v2 第二批已把 verdict 移到 ▲▼ 符號、數字墨色，這與 Chris 2026-08-03 在 `/daily` 拍板的判定色收斂同型（bullet 中性、✓/✕ 符號保色、DEMO 徽章誠實外框）。DEMO 目標的誠實標示（`.tag.demo`）同格在場，讀者知道基準是合成的。等 §26.6 Saltmarsh Assembly 訂單源接上，同一組符號直接承接真目標、零改碼。v1 零觸碰。ROADMAP「R11 待 Chris 裁」結案。

   **R22（`/output`／`/yield` 達標條）— 條留、三階面積色撤、改單一中性填色。**
   理由：(1) 條是資料標記（達標 %）、markup 自 2026-07-04 即有，v2 `display:block` 讓它真的畫出來是對的；(2) 但條的 good/warn/bad 是「以 DEMO 目標為基準的 verdict 塗在面積上」——`oee.demo_target` ＝ 實際產出 × 以站名為種子的 0.82–1.18 亂數（`board_oee.py:43-49`），色完全由站名決定、與表現無關，live 亦然（`_axis_page` 無 demo 閘）；(3) 同列距目標欄的 ▲▼ 已承載同一個 verdict，條再塗＝同一 verdict 兩通道；(4) `/daily` 前例：bullet／WIP 條→單一中性色、verdict 住符號（SPEC-DAILY-UNIT §判定色收斂，Chris 08-03）。故達標條改單一中性填色，`title` 帶百分比照舊（規則 39），數字由同列「距目標」欄提供（規則 25 的「bar 帶數字」以同列相鄰欄＋title 視為合規，寫進 SPEC）。**v1 空欄不修**：v1 使用者從未看過這條、修＝v1 行為變更且無益，#64「不是 bug」續留；v1 退役時自然消失。
   `/yield` 同模板同規則（`demo_yield_target` 98.5–99.5 同為合成）。

   規則 25 合規口徑：同列相鄰欄＋`title` 視為「bar 帶數字」。
