# -*- coding: utf-8 -*-
"""
board_tags.py — 產品能力標籤對照表（`{Plover, Kestrel, Heron}`）。規格＝`SPEC-PRODUCT-TAGS.md`。

★★★ GENERATED FILE — 由 `_gen_product_tags.py` 從 `_PRODUCT-TAGS-CENSUS-730D-V3-20260903.txt`
（md5 `9a3ae9b9d13bfac3d840ede4fe697f3a`、窗末 `2026-09-03`）產生，勿手改。★★★

**規則＝Chris 2026-08-31 裁決**（記憶 [[product-tags-rule]]）；**套用面＝2026-09-01 對話同意**
（SPEC §6）。要改標籤＝**重跑 730 天普查換來源檔 → 跑 `python _gen_product_tags.py` 重生
→ 人看過釘子**（C-PT1）。手改本檔＝下一次重生就被覆蓋，而且會讓「MES 決定、不人工裁」失效。

語意（SPEC §1）：
    標籤＝**能力**，不是排程也不是產出歸屬——「730 天內 MES 觀察到這台機跑過的 family」，
    跑過＝實務上被指派過。零訊號 ⇒ `origin="default"`＝R+A+M（全能力預設，**不是「沒有能力」**）。
    頁面**顯示相同**（default 與 observed 長一樣）、只有 tooltip／對照檔區分兩者。

規則（SPEC §2）：
    以 `(EID, station_class)` 為線；某 family 計入 observed ⇔
        `n_segments >= 50` **或** `n_segments / 該線總段數 >= 0.01`（**OR**；分母含 UNKNOWN）。
    再**按 EID 取跨站聯集**。UNKNOWN 永不成為標籤（C-PT2）。
    ⚠ 裁決原文寫「該機總段數」；本檔以**線級分母＋EID 聯集**實作——兩讀法在本次普查
    **等價（0 台變）**，立論與存證＝SPEC §2.1。

⚠ 鐵則（出處：〔裁決〕＝Chris 08-31；〔對話同意〕＝09-01）
    · C-PT1 不人工改本檔；改標籤＝重跑普查＋產生器。          〔裁決〕
    · C-PT2 UNKNOWN 永不成標籤（但進分母）。                  〔裁決〕
    · C-PT3 default 顯示與 observed 相同，只有 tooltip 區分。 〔裁決〕
    · C-PT4 標籤**只描述能力，不得用來過濾產出／良率**——那要用**段的 family**。〔對話同意〕
    · C-PT5 過濾器只在「列＝機台」的頁；狀態只在 URL 參數、預設全部、不跨頁記憶。〔對話同意〕

本檔＝**純資料＋純函式、零 I/O**。唯一的專案 import ＝ `mes_timeline` 的**兩張別名表**
（`EID_RENAME`／`EID_ALIAS_DROP`＝形④ 別名折疊的**唯一出處**，SPEC §2.5.3；純資料、
離線可用、**不碰 MES**）——**絕不在本檔另抄一份**，抄一份就是第二個會漂移的事實。
（`--selftest` 另會 import `_gen_product_tags` 的純函式重算比對，**不讀 MES**。）
行尾**永遠 LF**（`.gitattributes` 釘死）。

    python board_tags.py --selftest
"""

from __future__ import annotations

import re
import sys

from mes_timeline import EID_ALIAS_DROP, EID_RENAME

# ── 來源檔身分（SPEC §3）──────────────────────────────────────────────────────
CENSUS_FILE = "_PRODUCT-TAGS-CENSUS-730D-V3-20260903.txt"
CENSUS_MD5 = "9a3ae9b9d13bfac3d840ede4fe697f3a"
CENSUS_WINDOW_END = "2026-09-03"          # 主表最大 last_seen 的日期部分

# ── 規則常數（SPEC §2；改這裡＝改口徑，要走 SPEC 修訂）───────────────────────
WINDOW_DAYS = 730
TH_SEGMENTS = 50
TH_SHARE = 0.01
FAMILIES = ("Plover", "Kestrel", "Heron")
LETTER = {"Plover": "R", "Kestrel": "A", "Heron": "M"}
UNKNOWN_FAMILY = "UNKNOWN (NULL -- substrate/non-product segment)"

# ── 對照表 ────────────────────────────────────────────────────────────────────
# PRODUCT_TAGS: dict[str, dict]，鍵＝EID（形 `EID-` ＋ 五碼數字，補零）。每台：
#   tags       tuple[str]      能力標籤（FAMILIES 序）；origin="default" 時＝FAMILIES 全三個
#   origin     "observed"|"default"
#   families   dict[str, int]  產品 family **跨線合計原始段數**（不套門檻；沒看過的 family 不列）
#   stations   tuple[str]      這台機在普查窗內出現過的 station_class（排序）
#   first_seen str|None        只看產品 family 列的日期 min（YYYY-MM-DD）；一列都沒有 ⇒ None
#   last_seen  str|None        同上取 max
#   lines      tuple[tuple]    ((station, family, n_segments, share, counted), ...)
#                              share＝該 family 佔**該線**總段數（分母含 UNKNOWN），六位小數
#                              counted＝這一列有沒有通過門檻進聯集（UNKNOWN 恆 False）
PRODUCT_TAGS = {'EID-54306': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 615, 'Kestrel': 181, 'Heron': 102},
                              'stations': ('segCalibrationTest',),
                              'first_seen': '2025-08-20',
                              'last_seen': '2026-08-22',
                              'lines': (('segCalibrationTest', 'Plover', 615, 0.664147, True),
                                        ('segCalibrationTest', 'Kestrel', 181, 0.195464, True),
                                        ('segCalibrationTest', 'Heron', 102, 0.110151, True),
                                        ('segCalibrationTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         28,
                                         0.030238,
                                         False))},
                'EID-54318': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 2091},
                              'stations': ('segUVNitrogenCheck',),
                              'first_seen': '2024-12-24',
                              'last_seen': '2026-06-30',
                              'lines': (('segUVNitrogenCheck',
                                         'Plover',
                                         2091,
                                         1.0,
                                         True),)},
                'EID-54321': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 2308, 'Kestrel': 141},
                              'stations': ('segTopSealLube',),
                              'first_seen': '2026-03-04',
                              'last_seen': '2026-07-16',
                              'lines': (('segTopSealLube', 'Plover', 2308, 0.8, True),
                                        ('segTopSealLube', 'Kestrel', 141, 0.048873, True),
                                        ('segTopSealLube',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         436,
                                         0.151127,
                                         False))},
                'EID-54332': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 229, 'Kestrel': 97, 'Heron': 80},
                              'stations': ('segFramePlasma',),
                              'first_seen': '2025-08-17',
                              'last_seen': '2026-06-22',
                              'lines': (('segFramePlasma', 'Plover', 229, 0.564039, True),
                                        ('segFramePlasma', 'Kestrel', 97, 0.238916, True),
                                        ('segFramePlasma', 'Heron', 80, 0.197044, True))},
                'EID-54336': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1045, 'Kestrel': 330, 'Heron': 442},
                              'stations': ('segLamination',),
                              'first_seen': '2026-02-21',
                              'last_seen': '2026-06-24',
                              'lines': (('segLamination', 'Plover', 1045, 0.575124, True),
                                        ('segLamination', 'Kestrel', 330, 0.181618, True),
                                        ('segLamination', 'Heron', 442, 0.243258, True))},
                'EID-54341': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1187, 'Kestrel': 60, 'Heron': 60},
                              'stations': ('segFinalOQC',),
                              'first_seen': '2025-08-05',
                              'last_seen': '2026-07-28',
                              'lines': (('segFinalOQC', 'Plover', 1187, 0.908187, True),
                                        ('segFinalOQC', 'Kestrel', 60, 0.045907, True),
                                        ('segFinalOQC', 'Heron', 60, 0.045907, True))},
                'EID-54346': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1636, 'Kestrel': 341},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-12-21',
                              'last_seen': '2026-08-26',
                              'lines': (('segCleanTest', 'Plover', 1636, 0.798828, True),
                                        ('segCleanTest', 'Kestrel', 341, 0.166504, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         71,
                                         0.034668,
                                         False))},
                'EID-54348': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 526, 'Kestrel': 2063, 'Heron': 60},
                              'stations': ('segGrout', 'segSealDispense', 'segSealDispenseDamAndFill'),
                              'first_seen': '2026-03-10',
                              'last_seen': '2026-06-27',
                              'lines': (('segSealDispense', 'Plover', 526, 0.897611, True),
                                        ('segSealDispense', 'Heron', 60, 0.102389, True),
                                        ('segSealDispenseDamAndFill', 'Kestrel', 459, 1.0, True),
                                        ('segGrout', 'Kestrel', 1604, 0.984653, True),
                                        ('segGrout',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         25,
                                         0.015347,
                                         False))},
                'EID-54352': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1210, 'Kestrel': 393},
                              'stations': ('segPreCureAlignCheck',),
                              'first_seen': '2025-04-01',
                              'last_seen': '2026-08-12',
                              'lines': (('segPreCureAlignCheck', 'Plover', 1210, 0.674095, True),
                                        ('segPreCureAlignCheck', 'Kestrel', 393, 0.218942, True),
                                        ('segPreCureAlignCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         192,
                                         0.106964,
                                         False))},
                'EID-54358': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 774, 'Kestrel': 194},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-03-29',
                              'last_seen': '2026-06-20',
                              'lines': (('segCleanTest', 'Plover', 774, 0.799587, True),
                                        ('segCleanTest', 'Kestrel', 194, 0.200413, True))},
                'EID-54375': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 82},
                              'stations': ('segEOLChecker',),
                              'first_seen': '2025-01-02',
                              'last_seen': '2026-08-12',
                              'lines': (('segEOLChecker', 'Plover', 82, 0.009546, True),
                                        ('segEOLChecker',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         8508,
                                         0.990454,
                                         False))},
                'EID-54380': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 257, 'Kestrel': 126},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-05-21',
                              'last_seen': '2026-08-01',
                              'lines': (('segCleanTest', 'Plover', 257, 0.569845, True),
                                        ('segCleanTest', 'Kestrel', 126, 0.279379, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         68,
                                         0.150776,
                                         False))},
                'EID-54395': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 399, 'Kestrel': 83},
                              'stations': ('segCleanTest',),
                              'first_seen': '2026-02-24',
                              'last_seen': '2026-08-08',
                              'lines': (('segCleanTest', 'Plover', 399, 0.75, True),
                                        ('segCleanTest', 'Kestrel', 83, 0.156015, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         50,
                                         0.093985,
                                         False))},
                'EID-54400': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segAuxElectricalTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segAuxElectricalTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         5177,
                                         1.0,
                                         False),)},
                'EID-54411': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1229, 'Kestrel': 641},
                              'stations': ('segEOLRunner',),
                              'first_seen': '2025-09-18',
                              'last_seen': '2026-08-30',
                              'lines': (('segEOLRunner', 'Plover', 1229, 0.657219, True),
                                        ('segEOLRunner', 'Kestrel', 641, 0.342781, True))},
                'EID-54428': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 815, 'Kestrel': 119, 'Heron': 294},
                              'stations': ('segFrameBonding',),
                              'first_seen': '2026-03-30',
                              'last_seen': '2026-07-09',
                              'lines': (('segFrameBonding', 'Plover', 815, 0.663681, True),
                                        ('segFrameBonding', 'Kestrel', 119, 0.096906, True),
                                        ('segFrameBonding', 'Heron', 294, 0.239414, True))},
                'EID-54457': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1147, 'Kestrel': 309, 'Heron': 533},
                              'stations': ('segCalibrationTest',),
                              'first_seen': '2024-10-10',
                              'last_seen': '2026-08-11',
                              'lines': (('segCalibrationTest', 'Plover', 1147, 0.552771, True),
                                        ('segCalibrationTest', 'Kestrel', 309, 0.148916, True),
                                        ('segCalibrationTest', 'Heron', 533, 0.256867, True),
                                        ('segCalibrationTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         86,
                                         0.041446,
                                         False))},
                'EID-54466': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1105, 'Kestrel': 411, 'Heron': 446},
                              'stations': ('segLamination',),
                              'first_seen': '2025-04-06',
                              'last_seen': '2026-08-31',
                              'lines': (('segLamination', 'Plover', 1105, 0.535368, True),
                                        ('segLamination', 'Kestrel', 411, 0.199128, True),
                                        ('segLamination', 'Heron', 446, 0.216085, True),
                                        ('segLamination',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         102,
                                         0.049419,
                                         False))},
                'EID-54471': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 299, 'Kestrel': 2601, 'Heron': 166},
                              'stations': ('segGrout', 'segSealDispense', 'segSealDispenseDamAndFill'),
                              'first_seen': '2026-01-22',
                              'last_seen': '2026-07-03',
                              'lines': (('segSealDispense', 'Plover', 299, 0.643011, True),
                                        ('segSealDispense', 'Heron', 166, 0.356989, True),
                                        ('segSealDispenseDamAndFill', 'Kestrel', 449, 0.910751, True),
                                        ('segSealDispenseDamAndFill',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         44,
                                         0.089249,
                                         False),
                                        ('segGrout', 'Kestrel', 2152, 0.876221, True),
                                        ('segGrout',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         304,
                                         0.123779,
                                         False))},
                'EID-54506': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 345, 'Kestrel': 105},
                              'stations': ('segEOLRunner',),
                              'first_seen': '2024-11-19',
                              'last_seen': '2026-06-29',
                              'lines': (('segEOLRunner', 'Plover', 345, 0.766667, True),
                                        ('segEOLRunner', 'Kestrel', 105, 0.233333, True))},
                'EID-54508': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 599},
                              'stations': ('segStraightnessCheck',),
                              'first_seen': '2025-02-09',
                              'last_seen': '2026-09-03',
                              'lines': (('segStraightnessCheck',
                                         'Plover',
                                         599,
                                         0.880882,
                                         True),
                                        ('segStraightnessCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         81,
                                         0.119118,
                                         False))},
                'EID-54519': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 627},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2024-12-30',
                              'last_seen': '2026-07-28',
                              'lines': (('segLamLeakTest', 'Plover', 627, 0.864828, True),
                                        ('segLamLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         98,
                                         0.135172,
                                         False))},
                'EID-54573': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 248, 'Kestrel': 95, 'Heron': 78},
                              'stations': ('segModuleElectricalTest',),
                              'first_seen': '2025-12-31',
                              'last_seen': '2026-07-02',
                              'lines': (('segModuleElectricalTest', 'Plover', 248, 0.589074, True),
                                        ('segModuleElectricalTest', 'Kestrel', 95, 0.225653, True),
                                        ('segModuleElectricalTest', 'Heron', 78, 0.185273, True))},
                'EID-54597': {'tags': ('Plover', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 527, 'Heron': 194},
                              'stations': ('segSealDispense',),
                              'first_seen': '2024-09-25',
                              'last_seen': '2026-07-02',
                              'lines': (('segSealDispense', 'Plover', 527, 0.651422, True),
                                        ('segSealDispense', 'Heron', 194, 0.239802, True),
                                        ('segSealDispense',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         88,
                                         0.108776,
                                         False))},
                'EID-54618': {'tags': ('Plover', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 2336, 'Heron': 338},
                              'stations': ('segPreEncapPlasma',),
                              'first_seen': '2025-01-20',
                              'last_seen': '2026-08-21',
                              'lines': (('segPreEncapPlasma', 'Plover', 2336, 0.873598, True),
                                        ('segPreEncapPlasma', 'Heron', 338, 0.126402, True))},
                'EID-54625': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1356, 'Kestrel': 623},
                              'stations': ('segManifoldAttach',),
                              'first_seen': '2025-10-31',
                              'last_seen': '2026-08-21',
                              'lines': (('segManifoldAttach', 'Plover', 1356, 0.617486, True),
                                        ('segManifoldAttach', 'Kestrel', 623, 0.283698, True),
                                        ('segManifoldAttach',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         217,
                                         0.098816,
                                         False))},
                'EID-54645': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 769, 'Kestrel': 532},
                              'stations': ('segAssemblyLeakTest',),
                              'first_seen': '2024-11-07',
                              'last_seen': '2026-07-22',
                              'lines': (('segAssemblyLeakTest', 'Plover', 769, 0.530345, True),
                                        ('segAssemblyLeakTest', 'Kestrel', 532, 0.366897, True),
                                        ('segAssemblyLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         149,
                                         0.102759,
                                         False))},
                'EID-54655': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 894, 'Kestrel': 197, 'Heron': 60},
                              'stations': ('segWireBonding',),
                              'first_seen': '2024-11-04',
                              'last_seen': '2026-08-03',
                              'lines': (('segWireBonding', 'Plover', 894, 0.7152, True),
                                        ('segWireBonding', 'Kestrel', 197, 0.1576, True),
                                        ('segWireBonding', 'Heron', 60, 0.048, True),
                                        ('segWireBonding',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         99,
                                         0.0792,
                                         False))},
                'EID-54681': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segCalibrationTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segCalibrationTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         4471,
                                         1.0,
                                         False),)},
                'EID-54721': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 280, 'Kestrel': 127},
                              'stations': ('segPostCureAlignCheck',),
                              'first_seen': '2024-10-08',
                              'last_seen': '2026-07-29',
                              'lines': (('segPostCureAlignCheck', 'Plover', 280, 0.666667, True),
                                        ('segPostCureAlignCheck', 'Kestrel', 127, 0.302381, True),
                                        ('segPostCureAlignCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         13,
                                         0.030952,
                                         False))},
                'EID-54733': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 447, 'Kestrel': 205},
                              'stations': ('segFlexBonding',),
                              'first_seen': '2025-06-01',
                              'last_seen': '2026-08-20',
                              'lines': (('segFlexBonding', 'Plover', 447, 0.636752, True),
                                        ('segFlexBonding', 'Kestrel', 205, 0.292023, True),
                                        ('segFlexBonding',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         50,
                                         0.071225,
                                         False))},
                'EID-54748': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1185, 'Kestrel': 556},
                              'stations': ('segAttachCureOven',),
                              'first_seen': '2025-03-24',
                              'last_seen': '2026-07-21',
                              'lines': (('segAttachCureOven', 'Plover', 1185, 0.680643, True),
                                        ('segAttachCureOven', 'Kestrel', 556, 0.319357, True))},
                'EID-54760': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 510, 'Kestrel': 136},
                              'stations': ('segCleanTest',),
                              'first_seen': '2026-02-08',
                              'last_seen': '2026-08-26',
                              'lines': (('segCleanTest', 'Plover', 510, 0.700549, True),
                                        ('segCleanTest', 'Kestrel', 136, 0.186813, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         82,
                                         0.112637,
                                         False))},
                'EID-54769': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 735, 'Kestrel': 146, 'Heron': 261},
                              'stations': ('segFrameBonding',),
                              'first_seen': '2025-12-27',
                              'last_seen': '2026-09-03',
                              'lines': (('segFrameBonding', 'Plover', 735, 0.643608, True),
                                        ('segFrameBonding', 'Kestrel', 146, 0.127846, True),
                                        ('segFrameBonding', 'Heron', 261, 0.228546, True))},
                'EID-54775': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segCalibrationTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segCalibrationTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         4773,
                                         1.0,
                                         False),)},
                'EID-54791': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 357, 'Kestrel': 152},
                              'stations': ('segAuxElectricalTest',),
                              'first_seen': '2025-09-17',
                              'last_seen': '2026-08-03',
                              'lines': (('segAuxElectricalTest', 'Plover', 357, 0.690522, True),
                                        ('segAuxElectricalTest', 'Kestrel', 152, 0.294004, True),
                                        ('segAuxElectricalTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         8,
                                         0.015474,
                                         False))},
                'EID-54798': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1141, 'Kestrel': 665},
                              'stations': ('segFlexBonding',),
                              'first_seen': '2026-03-27',
                              'last_seen': '2026-08-08',
                              'lines': (('segFlexBonding', 'Plover', 1141, 0.529466, True),
                                        ('segFlexBonding', 'Kestrel', 665, 0.308585, True),
                                        ('segFlexBonding',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         349,
                                         0.161949,
                                         False))},
                'EID-54804': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segCleanTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         3352,
                                         1.0,
                                         False),)},
                'EID-54822': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {'Plover': 25},
                              'stations': ('segPostCureHeightCheck',),
                              'first_seen': '2026-04-05',
                              'last_seen': '2026-08-18',
                              'lines': (('segPostCureHeightCheck',
                                         'Plover',
                                         25,
                                         0.003092,
                                         False),
                                        ('segPostCureHeightCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         8060,
                                         0.996908,
                                         False))},
                'EID-54829': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 470, 'Kestrel': 70, 'Heron': 75},
                              'stations': ('segAutomatedOQC',),
                              'first_seen': '2025-06-18',
                              'last_seen': '2026-08-18',
                              'lines': (('segAutomatedOQC',
                                         'Plover',
                                         470,
                                         0.764228,
                                         True),
                                        ('segAutomatedOQC',
                                         'Kestrel',
                                         70,
                                         0.113821,
                                         True),
                                        ('segAutomatedOQC',
                                         'Heron',
                                         75,
                                         0.121951,
                                         True))},
                'EID-54890': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 686, 'Kestrel': 459, 'Heron': 116},
                              'stations': ('segSerialisation',),
                              'first_seen': '2025-02-04',
                              'last_seen': '2026-08-06',
                              'lines': (('segSerialisation', 'Plover', 686, 0.522468, True),
                                        ('segSerialisation', 'Kestrel', 459, 0.349581, True),
                                        ('segSerialisation', 'Heron', 116, 0.088347, True),
                                        ('segSerialisation',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         52,
                                         0.039604,
                                         False))},
                'EID-54891': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 771},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2025-04-22',
                              'last_seen': '2026-08-19',
                              'lines': (('segLamLeakTest', 'Plover', 771, 0.875142, True),
                                        ('segLamLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         110,
                                         0.124858,
                                         False))},
                'EID-54899': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 1830},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2025-09-18',
                              'last_seen': '2026-07-02',
                              'lines': (('segLamLeakTest', 'Plover', 1830, 1.0, True),)},
                'EID-54912': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1783, 'Kestrel': 475, 'Heron': 460},
                              'stations': ('segCalibrationTest',),
                              'first_seen': '2025-04-30',
                              'last_seen': '2026-06-26',
                              'lines': (('segCalibrationTest', 'Plover', 1783, 0.655997, True),
                                        ('segCalibrationTest', 'Kestrel', 475, 0.174761, True),
                                        ('segCalibrationTest', 'Heron', 460, 0.169242, True))},
                'EID-54950': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 357, 'Kestrel': 1819, 'Heron': 60},
                              'stations': ('segUVCure',
                                           'segUVCurePostDamAndFill',
                                           'segUVCurePostGrout'),
                              'first_seen': '2026-01-27',
                              'last_seen': '2026-06-26',
                              'lines': (('segUVCure', 'Plover', 357, 0.767742, True),
                                        ('segUVCure', 'Heron', 60, 0.129032, True),
                                        ('segUVCure',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         48,
                                         0.103226,
                                         False),
                                        ('segUVCurePostDamAndFill', 'Kestrel', 564, 0.809182, True),
                                        ('segUVCurePostDamAndFill',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         133,
                                         0.190818,
                                         False),
                                        ('segUVCurePostGrout', 'Kestrel', 1255, 0.926199, True),
                                        ('segUVCurePostGrout',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         100,
                                         0.073801,
                                         False))},
                'EID-54959': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1473, 'Kestrel': 452},
                              'stations': ('segThermalCure',),
                              'first_seen': '2024-12-10',
                              'last_seen': '2026-08-27',
                              'lines': (('segThermalCure', 'Plover', 1473, 0.703774, True),
                                        ('segThermalCure', 'Kestrel', 452, 0.215958, True),
                                        ('segThermalCure',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         168,
                                         0.080268,
                                         False))},
                'EID-54977': {'tags': ('Plover', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 333, 'Heron': 120},
                              'stations': ('segUVCure',),
                              'first_seen': '2025-09-24',
                              'last_seen': '2026-06-29',
                              'lines': (('segUVCure', 'Plover', 333, 0.698113, True),
                                        ('segUVCure', 'Heron', 120, 0.251572, True),
                                        ('segUVCure',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         24,
                                         0.050314,
                                         False))},
                'EID-54998': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 894},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2025-11-01',
                              'last_seen': '2026-07-16',
                              'lines': (('segLamLeakTest', 'Plover', 894, 0.887786, True),
                                        ('segLamLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         113,
                                         0.112214,
                                         False))},
                'EID-55032': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1108, 'Kestrel': 196, 'Heron': 475},
                              'stations': ('segDiePlacement',),
                              'first_seen': '2025-08-10',
                              'last_seen': '2026-07-11',
                              'lines': (('segDiePlacement', 'Plover', 1108, 0.561013, True),
                                        ('segDiePlacement', 'Kestrel', 196, 0.099241, True),
                                        ('segDiePlacement', 'Heron', 475, 0.240506, True),
                                        ('segDiePlacement',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         196,
                                         0.099241,
                                         False))},
                'EID-55040': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1898, 'Kestrel': 576, 'Heron': 463},
                              'stations': ('segCalibrationTest',),
                              'first_seen': '2024-11-28',
                              'last_seen': '2026-06-20',
                              'lines': (('segCalibrationTest', 'Plover', 1898, 0.646238, True),
                                        ('segCalibrationTest', 'Kestrel', 576, 0.196118, True),
                                        ('segCalibrationTest', 'Heron', 463, 0.157644, True))},
                'EID-55052': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 912},
                              'stations': ('segPostPickInspection',),
                              'first_seen': '2025-12-03',
                              'last_seen': '2026-08-18',
                              'lines': (('segPostPickInspection', 'Plover', 912, 0.892368, True),
                                        ('segPostPickInspection',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         110,
                                         0.107632,
                                         False))},
                'EID-55053': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 826},
                              'stations': ('segVacuumPackInspect',),
                              'first_seen': '2025-12-04',
                              'last_seen': '2026-07-18',
                              'lines': (('segVacuumPackInspect', 'Plover', 826, 0.843718, True),
                                        ('segVacuumPackInspect',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         153,
                                         0.156282,
                                         False))},
                'EID-55058': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 2461, 'Kestrel': 793, 'Heron': 374},
                              'stations': ('segAuxElectricalTest', 'segModuleElectricalTest'),
                              'first_seen': '2025-11-23',
                              'last_seen': '2026-08-10',
                              'lines': (('segAuxElectricalTest', 'Plover', 1179, 0.758199, True),
                                        ('segAuxElectricalTest', 'Kestrel', 138, 0.088746, True),
                                        ('segAuxElectricalTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         238,
                                         0.153055,
                                         False),
                                        ('segModuleElectricalTest', 'Plover', 1282, 0.512185, True),
                                        ('segModuleElectricalTest', 'Kestrel', 655, 0.261686, True),
                                        ('segModuleElectricalTest', 'Heron', 374, 0.149421, True),
                                        ('segModuleElectricalTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         192,
                                         0.076708,
                                         False))},
                'EID-55074': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1097, 'Kestrel': 568},
                              'stations': ('segCleanTest',),
                              'first_seen': '2024-10-14',
                              'last_seen': '2026-08-25',
                              'lines': (('segCleanTest', 'Plover', 1097, 0.636311, True),
                                        ('segCleanTest', 'Kestrel', 568, 0.329466, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         59,
                                         0.034223,
                                         False))},
                'EID-55109': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 216, 'Kestrel': 103, 'Heron': 60},
                              'stations': ('segWireBonding',),
                              'first_seen': '2024-10-16',
                              'last_seen': '2026-07-24',
                              'lines': (('segWireBonding', 'Plover', 216, 0.494279, True),
                                        ('segWireBonding', 'Kestrel', 103, 0.235698, True),
                                        ('segWireBonding', 'Heron', 60, 0.1373, True),
                                        ('segWireBonding',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         58,
                                         0.132723,
                                         False))},
                'EID-55130': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 1351, 'Kestrel': 245, 'Heron': 146},
                              'stations': ('segAutomatedOQC',),
                              'first_seen': '2025-07-02',
                              'last_seen': '2026-08-12',
                              'lines': (('segAutomatedOQC',
                                         'Plover',
                                         1351,
                                         0.672474,
                                         True),
                                        ('segAutomatedOQC',
                                         'Kestrel',
                                         245,
                                         0.121951,
                                         True),
                                        ('segAutomatedOQC',
                                         'Heron',
                                         146,
                                         0.072673,
                                         True),
                                        ('segAutomatedOQC',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         267,
                                         0.132902,
                                         False))},
                'EID-55142': {'tags': ('Kestrel',),
                              'origin': 'observed',
                              'families': {'Kestrel': 2311},
                              'stations': ('segPostLamInspection',),
                              'first_seen': '2025-03-09',
                              'last_seen': '2026-08-06',
                              'lines': (('segPostLamInspection', 'Kestrel', 2311, 0.879711, True),
                                        ('segPostLamInspection',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         316,
                                         0.120289,
                                         False))},
                'EID-55144': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 21},
                              'stations': ('segQASetup',),
                              'first_seen': '2025-08-28',
                              'last_seen': '2026-07-20',
                              'lines': (('segQASetup', 'Plover', 21, 0.021212, True),
                                        ('segQASetup',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         969,
                                         0.978788,
                                         False))},
                'EID-55177': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 879, 'Kestrel': 401},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-02-20',
                              'last_seen': '2026-07-27',
                              'lines': (('segCleanTest', 'Plover', 879, 0.686719, True),
                                        ('segCleanTest', 'Kestrel', 401, 0.313281, True))},
                'EID-55196': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 608, 'Kestrel': 274},
                              'stations': ('segCleanTest',),
                              'first_seen': '2026-02-17',
                              'last_seen': '2026-06-26',
                              'lines': (('segCleanTest', 'Plover', 608, 0.689342, True),
                                        ('segCleanTest', 'Kestrel', 274, 0.310658, True))},
                'EID-55216': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 477, 'Kestrel': 60},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-09-21',
                              'last_seen': '2026-08-24',
                              'lines': (('segCleanTest', 'Plover', 477, 0.888268, True),
                                        ('segCleanTest', 'Kestrel', 60, 0.111732, True))},
                'EID-55219': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 896, 'Kestrel': 230},
                              'stations': ('segThermalCure',),
                              'first_seen': '2025-06-13',
                              'last_seen': '2026-08-27',
                              'lines': (('segThermalCure', 'Plover', 896, 0.795737, True),
                                        ('segThermalCure',
                                         'Kestrel',
                                         230,
                                         0.204263,
                                         True))},
                'EID-55232': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 779, 'Kestrel': 274, 'Heron': 137},
                              'stations': ('segCalibrationTest',),
                              'first_seen': '2025-04-18',
                              'last_seen': '2026-06-25',
                              'lines': (('segCalibrationTest', 'Plover', 779, 0.654622, True),
                                        ('segCalibrationTest', 'Kestrel', 274, 0.230252, True),
                                        ('segCalibrationTest', 'Heron', 137, 0.115126, True))},
                'EID-55252': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 711, 'Kestrel': 168},
                              'stations': ('segPotting',),
                              'first_seen': '2025-09-15',
                              'last_seen': '2026-08-17',
                              'lines': (('segPotting', 'Plover', 711, 0.808874, True),
                                        ('segPotting', 'Kestrel', 168, 0.191126, True))},
                'EID-55272': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 2448, 'Kestrel': 155},
                              'stations': ('segTopSealLube',),
                              'first_seen': '2026-03-02',
                              'last_seen': '2026-07-20',
                              'lines': (('segTopSealLube', 'Plover', 2448, 0.799477, True),
                                        ('segTopSealLube', 'Kestrel', 155, 0.050621, True),
                                        ('segTopSealLube',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         459,
                                         0.149902,
                                         False))},
                'EID-55286': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 406, 'Kestrel': 177},
                              'stations': ('segPreCureHeightCheck',),
                              'first_seen': '2025-04-05',
                              'last_seen': '2026-08-20',
                              'lines': (('segPreCureHeightCheck',
                                         'Plover',
                                         406,
                                         0.694017,
                                         True),
                                        ('segPreCureHeightCheck',
                                         'Kestrel',
                                         177,
                                         0.302564,
                                         True),
                                        ('segPreCureHeightCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         2,
                                         0.003419,
                                         False))},
                'EID-55301': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segModuleElectricalTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segModuleElectricalTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         4313,
                                         1.0,
                                         False),)},
                'EID-55321': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'default',
                              'families': {},
                              'stations': ('segBendTest',),
                              'first_seen': None,
                              'last_seen': None,
                              'lines': (('segBendTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         689,
                                         1.0,
                                         False),)},
                'EID-55338': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 981},
                              'stations': ('segVacuumPackInspect',),
                              'first_seen': '2025-08-11',
                              'last_seen': '2026-08-13',
                              'lines': (('segVacuumPackInspect', 'Plover', 981, 1.0, True),)},
                'EID-55352': {'tags': ('Plover', 'Kestrel', 'Heron'),
                              'origin': 'observed',
                              'families': {'Plover': 470, 'Kestrel': 145, 'Heron': 196},
                              'stations': ('segDiePlacement',),
                              'first_seen': '2025-10-19',
                              'last_seen': '2026-07-23',
                              'lines': (('segDiePlacement', 'Plover', 470, 0.579531, True),
                                        ('segDiePlacement', 'Kestrel', 145, 0.178792, True),
                                        ('segDiePlacement', 'Heron', 196, 0.241677, True))},
                'EID-55357': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 1914},
                              'stations': ('segStraightnessCheck',),
                              'first_seen': '2025-08-19',
                              'last_seen': '2026-06-26',
                              'lines': (('segStraightnessCheck',
                                         'Plover',
                                         1914,
                                         0.966667,
                                         True),
                                        ('segStraightnessCheck',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         66,
                                         0.033333,
                                         False))},
                'EID-55411': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 364, 'Kestrel': 178},
                              'stations': ('segPlasmaActivation',),
                              'first_seen': '2025-09-10',
                              'last_seen': '2026-08-21',
                              'lines': (('segPlasmaActivation', 'Plover', 364, 0.671587, True),
                                        ('segPlasmaActivation', 'Kestrel', 178, 0.328413, True))},
                'EID-55459': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 393, 'Kestrel': 72},
                              'stations': ('segAttachCureOven',),
                              'first_seen': '2025-04-16',
                              'last_seen': '2026-07-21',
                              'lines': (('segAttachCureOven', 'Plover', 393, 0.813665, True),
                                        ('segAttachCureOven', 'Kestrel', 72, 0.149068, True),
                                        ('segAttachCureOven',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         18,
                                         0.037267,
                                         False))},
                'EID-55462': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 587},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2025-06-07',
                              'last_seen': '2026-07-06',
                              'lines': (('segLamLeakTest', 'Plover', 587, 0.900307, True),
                                        ('segLamLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         65,
                                         0.099693,
                                         False))},
                'EID-55508': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 537},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2026-03-08',
                              'last_seen': '2026-07-01',
                              'lines': (('segLamLeakTest', 'Plover', 537, 1.0, True),)},
                'EID-55513': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 508, 'Kestrel': 301},
                              'stations': ('segCleanTest',),
                              'first_seen': '2025-07-30',
                              'last_seen': '2026-07-26',
                              'lines': (('segCleanTest', 'Plover', 508, 0.627936, True),
                                        ('segCleanTest', 'Kestrel', 301, 0.372064, True))},
                'EID-55518': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 346, 'Kestrel': 145},
                              'stations': ('segAuxElectricalTest',),
                              'first_seen': '2025-01-16',
                              'last_seen': '2026-07-16',
                              'lines': (('segAuxElectricalTest', 'Plover', 346, 0.704684, True),
                                        ('segAuxElectricalTest', 'Kestrel', 145, 0.295316, True))},
                'EID-55528': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 780},
                              'stations': ('segLamLeakTest',),
                              'first_seen': '2024-11-25',
                              'last_seen': '2026-08-04',
                              'lines': (('segLamLeakTest', 'Plover', 780, 0.989848, True),
                                        ('segLamLeakTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         8,
                                         0.010152,
                                         False))},
                'EID-55546': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 561, 'Kestrel': 242},
                              'stations': ('segCleanTest',),
                              'first_seen': '2024-12-02',
                              'last_seen': '2026-08-23',
                              'lines': (('segCleanTest', 'Plover', 561, 0.691739, True),
                                        ('segCleanTest', 'Kestrel', 242, 0.298397, True),
                                        ('segCleanTest',
                                         'UNKNOWN (NULL -- substrate/non-product segment)',
                                         8,
                                         0.009864,
                                         False))},
                'EID-55561': {'tags': ('Plover',),
                              'origin': 'observed',
                              'families': {'Plover': 1275},
                              'stations': ('segPreLamPlasma',),
                              'first_seen': '2025-07-09',
                              'last_seen': '2026-07-11',
                              'lines': (('segPreLamPlasma',
                                         'Plover',
                                         1275,
                                         1.0,
                                         True),)},
                'EID-55564': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 453, 'Kestrel': 140},
                              'stations': ('segChassisAttach',),
                              'first_seen': '2025-08-16',
                              'last_seen': '2026-07-23',
                              'lines': (('segChassisAttach', 'Plover', 453, 0.763912, True),
                                        ('segChassisAttach', 'Kestrel', 140, 0.236088, True))},
                'EID-55593': {'tags': ('Plover', 'Kestrel'),
                              'origin': 'observed',
                              'families': {'Plover': 1154, 'Kestrel': 609},
                              'stations': ('segFlexBonding',),
                              'first_seen': '2025-09-11',
                              'last_seen': '2026-08-06',
                              'lines': (('segFlexBonding', 'Plover', 1154, 0.654566, True),
                                        ('segFlexBonding', 'Kestrel', 609, 0.345434, True))}}


# ══════════════════════════════════════════════════════════════════════════════
# 形④ 別名折疊（SPEC §2.5.3；Chris 2026-09-02 裁）
# ══════════════════════════════════════════════════════════════════════════════
# **改名型（形④機制 (a)）的主 EID，標籤取其別名的 `observed` 聯集。**
# 理由：改名主 EID 在 MES 上**沒有自己的身分**——段全記在別名上（`EID-38106` 的段記在
# `38041`／`38072`）⇒ 拿 §2.5 的 default 代表它，是把一個**已知的觀測**講成**無訊號**。
#
# ⚠ 別名表**只 import `mes_timeline` 的同一個物件**（`--selftest` 釘 `is`）——
#   在本檔另抄一張表＝第二個會漂移的事實（同 `_probe_cost_rollup`／`board_capacity` 紀律）。
# ⚠ **只限機制 (a)**：`ALIAS_OF` 的鍵恆 ⊆ `set(EID_RENAME.values())`；機制 (b) 的主 EID
#   （`07940`／`07905`／`26554`）**兩張表都沒有它們**、本來就有自己的超集段
#   ⇒ **一個位元不動**（SPEC §2.5.2／§2.5.3 的警告）。
# ⚠ **不吃吸收元（§7 A11）**：只聯集 `origin == "observed"` 的別名。若一顆 observed 別名都沒有，
#   折疊回 `None` ⇒ 照走 §2.5 的 default——**寧可誠實地說「沒量到」，也不假裝有根據**。
# ⚠ `PRODUCT_TAGS` **仍是純普查**（鍵＝普查窗內出現過的 EID）；折疊結果住 `FOLDED_TAGS`。
#   要拿一台機的記錄（tooltip 段數等）請用 `record_of()`，**不要直接 `PRODUCT_TAGS.get()`**。
# ⚠⚠ **標籤取聯集、段數不取和**：機制 (b) 的別名（`EID_ALIAS_DROP`，如 `38072`）段是主 EID 段的
#   **子集** ⇒ 只貢獻標籤／站別／first-last，**不貢獻段數與線**（`SPEC-RUNWINDOW.md` §10.8-8
#   「不是加總。加總＝雙算」）。段數只由**計數主**（`main` 自己＋`EID_RENAME` 來源）貢獻。


def _alias_of():
    """`{改名型主 EID: (別名, ...)}`。別名＝兩張表中 value 指向該主 EID 的 key。"""
    mains = set(EID_RENAME.values())
    out = {}
    for table in (EID_RENAME, EID_ALIAS_DROP):
        for alias, main in table.items():
            if main in mains:
                out.setdefault(main, set()).add(alias)
    return {m: tuple(sorted(a)) for m, a in out.items()}


ALIAS_OF = _alias_of()


def _fold_alias(main, aliases):
    """把 `main` 自己（若有）＋ `aliases` 中**observed** 的普查列聯集成一筆記錄。

    ★★ **段數絕不相加**（`SPEC-RUNWINDOW.md` §10.8-8 的兩機制表；§10.8-5 反模式 2）：

      · **計數主**＝`main` 自己 ＋ **`EID_RENAME` 來源**的別名（機制 (a)「**搬**」——
        主 EID 本來零段、段被 fixture 領走）⇒ 貢獻 `families` 段數與 `lines`。
      · **`EID_ALIAS_DROP` 來源**的別名（機制 (b)「**忽略**」——別名段是主 EID 段的
        **子集**、每段同掛）⇒ **只貢獻 `tags`／`stations`／`first_seen`／`last_seen`，
        不貢獻段數、不貢獻線**。相加＝**雙算**（`38041` 155 ＋ `38072` 141 ＝ 296
        ≈ 2× 真值；普查兩列 first/last 與 lot 樣本逐位相同＝同一批段被記兩次）。

    站集合聯集、`first_seen`／`last_seen` 取 min/max、`tags` 取聯集（FAMILIES 序）、
    `origin` 恆 `"observed"`。零貢獻者 ⇒ 回 `None`（呼叫端照走 default）。
    ⚠ Chris 的裁決（SPEC §2.5.3）是「標籤取別名 observed 的**聯集**」——**聯集講的是標籤**，
      段數的口徑住 §10.8-8，兩者別混。
    """
    parts = []
    for eid in (main,) + tuple(aliases):
        rec = PRODUCT_TAGS.get(eid)
        if rec is not None and rec["origin"] == "observed":
            parts.append((eid, rec))
    if not parts:
        return None
    fam, lines, stations, tagset, counted = {}, [], set(), set(), []
    for eid, rec in parts:
        stations.update(rec["stations"])
        tagset.update(rec["tags"])
        if eid != main and eid not in EID_RENAME:
            continue                      # 機制 (b)：別名段不另計（絕不相加）
        counted.append(eid)
        for f, n in rec["families"].items():
            fam[f] = fam.get(f, 0) + n
        lines.extend(rec["lines"])
    firsts = [rec["first_seen"] for _e, rec in parts if rec["first_seen"]]
    lasts = [rec["last_seen"] for _e, rec in parts if rec["last_seen"]]
    src = tuple(e for e, _r in parts)
    cnt = tuple(counted)
    return {
        "tags": tuple(f for f in FAMILIES if f in tagset),
        "origin": "observed",
        "families": {f: fam[f] for f in FAMILIES if f in fam},
        "stations": tuple(sorted(stations)),
        "first_seen": min(firsts) if firsts else None,
        "last_seen": max(lasts) if lasts else None,
        "lines": tuple(sorted(lines)),
        # 折疊來源＝哪幾顆別名（含主 EID 自身，若它也有普查列）。只供人看／log。
        "folded_from": src,
        # 其中**貢獻段數**的是哪幾顆（機制 (a)）；其餘只貢獻標籤。
        "counted_from": cnt,
        "facts": ("folded from " + ", ".join(src)
                  + "; segments counted from " + (", ".join(cnt) or "(none)")),
    }


FOLDED_TAGS = {m: r for m, r in
               ((m, _fold_alias(m, a)) for m, a in ALIAS_OF.items()) if r is not None}


def record_of(eid):
    """`eid` → 對照記錄（**含形④ 折疊**）；不在表內 ⇒ `None`。

    折疊主 EID 走 `FOLDED_TAGS`，其餘走普查表 `PRODUCT_TAGS`。
    ⚠ 呈現層要段數（chip tooltip）**一律走本函式**——直接讀 `PRODUCT_TAGS` 會讓折疊主 EID
    的 chip 是 observed、tooltip 卻空白。
    """
    rec = FOLDED_TAGS.get(eid)
    return rec if rec is not None else PRODUCT_TAGS.get(eid)


# ══════════════════════════════════════════════════════════════════════════════
# 查詢層（純函式）
# ══════════════════════════════════════════════════════════════════════════════

def normalize_families(families):
    """任意 family 寫法 → canonical tuple（**FAMILIES 序、去重**）。

    吃字母（`R`/`a`/`m`）與全名（`kestrel`／`Plover`），**大小寫不敏感**；
    **不認得的一律忽略**（不炸——URL 參數是使用者可以亂打的東西）。
    `None`／空 ⇒ `()`。

    ⚠ 傳 `str` 會 raise（腳槍：`"Plover"` 逐字元迭代會意外「剛好」只中 R）。
    要吃字串請走 `parse_tag_param()`。
    """
    if families is None:
        return ()
    if isinstance(families, str):
        raise TypeError("normalize_families 不吃 str（會逐字元迭代）；"
                        "要解析 URL 參數請用 parse_tag_param()")
    lookup = {}
    for fam in FAMILIES:
        lookup[fam.lower()] = fam
        lookup[LETTER[fam].lower()] = fam
    got = set()
    for x in families:
        if not isinstance(x, str):
            continue
        fam = lookup.get(x.strip().lower())
        if fam:
            got.add(fam)
    return tuple(f for f in FAMILIES if f in got)


def parse_tag_param(s):
    """URL 參數 `?tag=` → canonical family tuple（SPEC §6.2）。

        parse_tag_param("R,a")     -> ("Plover", "Kestrel")
        parse_tag_param("kestrel") -> ("Kestrel",)
        parse_tag_param("m , R")   -> ("Plover", "Heron")     # 回傳恆 FAMILIES 序
        parse_tag_param("R,zzz")   -> ("Plover",)             # 不認得的忽略
        parse_tag_param("")        -> ()                      # 空＝全部（呼叫端語意）
        parse_tag_param(None)      -> ()

    分隔符：逗號、加號、空白（三者混用皆可）。**空 tuple ＝「沒選」＝ 全部**——
    `filter_eids` 對空選回全部，兩支的語意在此對齊（C-PT5「預設全部」）。
    """
    if s is None:
        return ()
    if not isinstance(s, str):
        return normalize_families(s)
    return normalize_families([t for t in re.split(r"[,+\s]+", s) if t])


def tags_of(eid):
    """`eid`（形 `EID-` ＋ 五碼數字，補零）→ `(tags_tuple, origin)`。

    不在表內（名冊有、普查沒有的結構性零訊號機：治具／烤箱／顯微鏡…）
    ⇒ `(FAMILIES, "default")`——與表內 default 台同形，呼叫端不必分兩條路。
    鍵**大小寫敏感、不做正規化**：`"eid-00425"`／`"EID-425"` 一律視為表外 ⇒ default。
    ★ 形④ 改名型主 EID（如 `EID-38106`）走 `FOLDED_TAGS`＝其別名 `observed` 的聯集
    （SPEC §2.5.3）——它的段記在別名編號上，不是「沒量到」。
    """
    rec = record_of(eid)
    if rec is None:
        return FAMILIES, "default"
    return rec["tags"], rec["origin"]


def letters_of(eid):
    """`eid` → 縮寫字母串（FAMILIES 序）。例：Plover+Kestrel ⇒ `"RA"`；default ⇒ `"RAM"`。"""
    tags, _origin = tags_of(eid)
    return "".join(LETTER[f] for f in FAMILIES if f in tags)


def has_tag(eid, family):
    """這台機有沒有 `family` 能力（default 台恆 True——零訊號＝全能力預設）。

    `family` 吃字母或全名、大小寫不敏感；**不認得或不是單一 family ⇒ False**（不炸）。
    """
    want = normalize_families([family]) if isinstance(family, str) else ()
    if len(want) != 1:
        return False
    tags, _origin = tags_of(eid)
    return want[0] in tags


def filter_eids(eids, families):
    """`eids` 依 `families` 過濾（**OR** 語意；空選＝全部）。回 list，**保留輸入順序**。

    · `families` 吃字母或全名（`["R", "kestrel"]` 可）；不認得的忽略；
      濾掉之後空掉 ⇒ 視同空選＝**全部**（不會回空表）。
    · default 台（含不在表內的）帶全三個標籤 ⇒ **任何選擇都會中**（C-PT3 的必然結果：
      「不知道」不等於「不能跑」，不該因為沒訊號就從清單消失）。
    · `eids`／`families` 傳 `str` 會 raise（逐字元迭代的腳槍）；generator 可以，
      family 集合只算一次。
    """
    if isinstance(eids, str):
        raise TypeError("filter_eids 的 eids 不吃 str（會逐字元迭代）；請傳 list/tuple/generator")
    want = normalize_families(families)
    if not want:
        return list(eids)
    fs = set(want)
    return [e for e in eids if fs & set(tags_of(e)[0])]


# ══════════════════════════════════════════════════════════════════════════════
# selftest
# ══════════════════════════════════════════════════════════════════════════════

def selftest():
    n = [0]

    def ck(cond, msg):
        n[0] += 1
        if not cond:
            raise AssertionError(f"[{n[0]}] {msg}")

    # ── 形狀 ──────────────────────────────────────────────────────────────────
    ck(FAMILIES == ("Plover", "Kestrel", "Heron"), "FAMILIES 順序＝R/A/M（tuple 順序全檔依此）")
    ck(WINDOW_DAYS == 730 and TH_SEGMENTS == 50 and TH_SHARE == 0.01,
       "規則常數＝730 天／50 段／1%（改它要走 SPEC 修訂）")
    ck(set(LETTER) == set(FAMILIES) and "".join(LETTER[f] for f in FAMILIES) == "RAM",
       "LETTER 覆蓋三個 family、序＝RAM")
    ck(CENSUS_FILE == "_PRODUCT-TAGS-CENSUS-730D-V3-20260903.txt" and CENSUS_MD5 == "9a3ae9b9d13bfac3d840ede4fe697f3a",
       "來源檔身分（SPEC §3）")
    ck(CENSUS_WINDOW_END == "2026-09-03", "窗末＝主表最大 last_seen 日期")

    for eid, rec in PRODUCT_TAGS.items():
        ck(isinstance(eid, str) and eid.startswith("EID-") and len(eid) == 9,
           f"EID 形狀 {eid}")
        ck(rec["origin"] in ("observed", "default"), f"{eid} origin enum")
        ck(rec["tags"] == tuple(f for f in FAMILIES if f in set(rec["tags"])),
           f"{eid} tags 依 FAMILIES 序")
        ck(all(f in FAMILIES for f in rec["tags"]), f"{eid} tags 只含 FAMILIES")
        ck(bool(rec["tags"]), f"{eid} tags 非空（default 台＝全三個）")
        if rec["origin"] == "default":
            ck(rec["tags"] == FAMILIES, f"{eid} default ⇒ R+A+M")
        ck(all(f in FAMILIES for f in rec["families"]), f"{eid} families 只含產品 family")
        ck(UNKNOWN_FAMILY not in rec["families"], f"{eid} UNKNOWN 不進 families（C-PT2）")
        ck(all(not c for (_st, f, _n, _s, c) in rec["lines"] if f == UNKNOWN_FAMILY),
           f"{eid} UNKNOWN 列 counted 恆 False（C-PT2）")
        ck(tuple(sorted(set(st for (st, _f, _n, _s, _c) in rec["lines"]))) == rec["stations"],
           f"{eid} stations ≡ lines 的站集合")

    # ── 普查規模釘子（★ 人工承認值；換普查檔就會紅＝設計）────────────────────
    # WP-10d（R-P11）：公開樹的普查表是合成的（EID 數／線數／observed:default 比例／具名樣本機都不同）⇒
    #   具名計數與具名樣本只在私有樹照釘；公開樹改驗表自身的結構不變式，並由表推導「樣本機」的角色。
    import core_paths as _wp
    _pub = _wp.is_public_tree()
    if not _pub:
        import board_private_pins as _pp
    n_eids = len(PRODUCT_TAGS)
    n_lines = sum(len(r["stations"]) for r in PRODUCT_TAGS.values())
    obs = sorted(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed")
    dft = sorted(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "default")
    ck(_pub or n_eids == _pp.PINS["tags.census_eids_n"], f"普查 EID 數應與私有釘值相同，實 {n_eids}")
    ck(_pub or n_lines == _pp.PINS["tags.census_lines_n"], f"(EID, station) 線數應與私有釘值相同，實 {n_lines}")
    ck(len(obs) + len(dft) == n_eids and len(dft) > 0
       and (_pub or (len(obs) == _pp.PINS["tags.observed_n"] and len(dft) == _pp.PINS["tags.default_n"])),
       f"observed/default 數應與私有釘值相同，實 {len(obs)}/{len(dft)}")
    ck(_pub or dft == _pp.PINS["tags.default_eids"], f"default 名單應與私有釘值相同，實 {dft}")

    # 線級 tag 集合分布（零訊號線投影成 R+A+M——它們最終落在 default 台上）
    dist = {}
    for rec in PRODUCT_TAGS.values():
        per_station = {}
        for (st, f, _nn, _s, c) in rec["lines"]:
            per_station.setdefault(st, set())
            if c:
                per_station[st].add(f)
        for st, s in per_station.items():
            key = tuple(f for f in FAMILIES if f in s) or FAMILIES
            dist[key] = dist.get(key, 0) + 1
    ck(_pub or dist == _pp.PINS["tags.line_family_distribution"], f"線級 tag 集合分布，實 {dist}")
    ck(("Heron",) not in dist, "零 Heron-only 線（Heron 永遠伴隨 Plover；SPEC §4 觀察）")

    # ── 抽查（SPEC §4 表的五個定錨）──────────────────────────────────────────
    if not _pub:   # 具名樣本機的事實（私有普查）；公開樹由下面的角色推導取代
        ck(tags_of("EID-55142") == (("Kestrel",), "observed"), "EID-16821＝Kestrel only")
        ck(PRODUCT_TAGS["EID-55142"]["families"]["Kestrel"] == _pp.PINS["tags.eid_16821_kestrel_segments_n"],
           "EID-16821 Kestrel 段數")
        ck(tags_of("EID-26596") == (("Kestrel",), "observed"), "EID-26596＝Kestrel only")
        ck(tags_of("EID-00425") == (("Plover",), "observed"), "EID-00425＝Plover only")
        ck(tags_of("EID-00643") == (("Plover", "Kestrel"), "observed"), "EID-00643＝Plover+Kestrel")
        ck(tags_of("EID-11860") == (("Plover",), "observed"),
           "EID-11860＝Plover（segPostPickInspection 線全 UNKNOWN、DiePlacement 線是 Plover "
           "⇒ **EID 級聯集後 observed**，不是 default）")
        ck(any(f == UNKNOWN_FAMILY for (_st, f, _nn, _s, _c) in PRODUCT_TAGS["EID-11860"]["lines"]),
           "EID-11860 確實有 UNKNOWN 列（上一條的前提）")

    # 樣本角色：私有樹＝具名機台；公開樹＝由合成普查表推導（R-only／R+A／A-only 各一台 observed）
    if _pub:
        _Diag = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == (FAMILIES[0],))
        _RA = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == FAMILIES[:2])
        _AO = next(e for e, r in PRODUCT_TAGS.items() if r["origin"] == "observed" and tuple(r["tags"]) == (FAMILIES[1],))
    else:
        _Diag, _RA, _AO = "EID-00425", "EID-00643", "EID-55142"
    assert _pub or (_Diag, _RA, _AO) == ("EID-00425", "EID-00643", "EID-55142")      # 角色綁定（不計入斷言數）
    # ── 形④ 別名折疊（SPEC §2.5.3；Chris 2026-09-02 裁）────────────────────────
    import mes_timeline as _mt
    ck(EID_RENAME is _mt.EID_RENAME and EID_ALIAS_DROP is _mt.EID_ALIAS_DROP,
       "★★ 別名表 **import 同一個物件**（本檔不得另抄一份＝第二個會漂移的事實）")
    ck(set(ALIAS_OF) == set(EID_RENAME.values()),
       f"ALIAS_OF 只含機制 (a) 改名型主 EID，實 {sorted(ALIAS_OF)}")
    import board_capacity as _wcp
    _fm = next(iter(EID_RENAME.values()))
    _ren = next(iter(EID_RENAME))
    _fa = tuple(ALIAS_OF[_fm])
    _b_list = tuple(_wcp._ALIGNER_ALIASES.values())      # 機制 (b) 主 EID（私有樹＝07940／07905／26554）
    _exp_tags = tuple(f for f in FAMILIES
                      if any(f in PRODUCT_TAGS[a]["tags"] for a in _fa if PRODUCT_TAGS[a]["origin"] == "observed"))
    ck(ALIAS_OF.get(_fm) == _fa and (_pub or (_fm == "EID-55376" and _fa == ("EID-55272", "EID-54321"))),
       f"{_fm} 的別名＝rename ＋ drop 兩顆，實 {ALIAS_OF.get(_fm)}")
    ck(all(m not in PRODUCT_TAGS for m in ALIAS_OF),
       "改名主 EID 在普查表內沒有自己的列（＝本規則存在的理由；MES 段記在別名編號上）")
    ck(set(FOLDED_TAGS) == set(ALIAS_OF), f"每個改名主 EID 都折得出記錄，實 {sorted(FOLDED_TAGS)}")
    for _b in _b_list:
        ck(_b not in ALIAS_OF and _b not in FOLDED_TAGS,
           f"機制 (b) 主 EID {_b} 完全不受折疊影響（SPEC §2.5.3：它們本來就有自己的超集段）")
    ck(tags_of(_b_list[-1]) == ((tuple(PRODUCT_TAGS[_b_list[-1]]["tags"]), PRODUCT_TAGS[_b_list[-1]]["origin"])
                               if _b_list[-1] in PRODUCT_TAGS else (FAMILIES, "default"))
       and (_pub or tags_of(_b_list[-1]) == (FAMILIES, "default")),
       "26554 照舊 default（機制 (b) 一個位元不動）")
    _fold = record_of(_fm)
    ck(_fold is not None and _fold["origin"] == "observed",
       f"{_fm} 折疊後＝observed（不是「沒量到」，是「記在別的編號上」）")
    ck(tags_of(_fm) == (_exp_tags, "observed"),
       f"{_fm} 標籤＝別名 observed 聯集，實 {tags_of(_fm)}")
    ck(_fold["families"] == PRODUCT_TAGS[_ren]["families"]
       and (_pub or _fold["families"] == {"Kestrel": _pp.PINS["tags.folded_kestrel_segments_n"]}),
       "★★ 折疊段數＝**取計數主**（EID-38041）、別名段不另計"
       f"（SPEC-RUNWINDOW §10.8-8 機制 (b)：加總＝雙算），實 {_fold['families']}")
    ck(_fold["counted_from"] == (_ren,) and (_pub or _fold["counted_from"] == ("EID-55272",)),
       f"counted_from＝只有 EID_RENAME 來源貢獻段數，實 {_fold['counted_from']}")
    ck(all(a not in EID_RENAME or a in _fold["counted_from"] for a in _fa)
       and all(a not in _fold["counted_from"] for a in _fa if a in EID_ALIAS_DROP),
       "EID_ALIAS_DROP 的別名一顆都不進計數（子集、不另計）")
    ck(_fold["folded_from"] == _fa and all(a in _fold["facts"] for a in _fa),
       f"facts／folded_from 註明來自哪些別名，實 {_fold['facts']!r}")
    _al = [PRODUCT_TAGS[a] for a in _fa]
    _cm = [PRODUCT_TAGS[a] for a in _fold["counted_from"]]
    ck(_fold["families"]["Kestrel"] < sum(r["families"]["Kestrel"] for r in _al),
       "★ 反雙算釘：折疊段數**嚴格小於**兩顆別名相加（相加曾是本檔的 bug）")
    ck(_fold["stations"] == tuple(sorted(set(s for r in _al for s in r["stations"])))
       and _fold["first_seen"] == min(r["first_seen"] for r in _al)
       and _fold["last_seen"] == max(r["last_seen"] for r in _al),
       "站集合／標籤聯集、first/last 取 min/max（這三項吃全部 observed 別名）")
    ck(len(_fold["lines"]) == sum(len(r["lines"]) for r in _cm),
       "lines＝只有計數主的列（機制 (b) 別名的線不接上來）")
    _nf = next((f for f in FAMILIES if f not in _exp_tags), None)
    ck(letters_of(_fm) == "".join(LETTER[f] for f in _exp_tags) and has_tag(_fm, LETTER[_exp_tags[0]])
       and (_nf is None or not has_tag(_fm, _nf)) and (_pub or (letters_of(_fm) == "A" and not has_tag(_fm, "Plover"))),
       "letters_of／has_tag 一併吃到折疊結果（都走 tags_of）")
    ck(_fm not in PRODUCT_TAGS,
       "PRODUCT_TAGS 仍是**純普查**（折疊結果只住 FOLDED_TAGS）")
    for _a in _fa:
        ck(record_of(_a) is PRODUCT_TAGS[_a], f"別名 {_a} 自身仍回它自己的普查列、不被折疊改寫")
    ck(record_of("EID-99999") is None, "表外 ⇒ record_of 回 None")
    ck(_fold_alias("EID-99999", ()) is None,
       "零貢獻者 ⇒ None ⇒ 照走 §2.5 的 default（不假裝有根據）")
    ck(_fold_alias("EID-99999", (dft[0],)) is None,
       "★ 只吃 observed 別名：default 別名不進聯集（§7 A11 吸收元陷阱）")

    # ── 查詢層 ────────────────────────────────────────────────────────────────
    ck(tags_of("EID-99999") == (FAMILIES, "default"), "不在表內 ⇒ (FAMILIES, default)")
    ck(tags_of("") == (FAMILIES, "default"), "空字串一樣走 default，不炸")
    ck(tags_of(_Diag.lower()) == (FAMILIES, "default"), "鍵大小寫敏感、不正規化 ⇒ 走 default")
    ck(letters_of(_RA) == "RA", "letters_of＝FAMILIES 序縮寫")
    ck(letters_of("EID-99999") == "RAM", "不在表內 ⇒ RAM")
    ck(letters_of(dft[0]) == "RAM", "表內 default 台 ⇒ RAM（與表外同形）")
    ck(has_tag(_Diag, "Plover") and not has_tag(_Diag, "Heron"),
       "has_tag 正負各一")
    ck(has_tag(_Diag, "R") and has_tag(_Diag, "plover"),
       "has_tag 吃字母與小寫全名")
    ck(has_tag("EID-99999", "Heron"), "不在表內 ⇒ 三個 family 都 True")
    ck(not has_tag(_Diag, "Nessie"), "不認得的 family ⇒ False（不炸）")
    ck(not has_tag(_Diag, "RA") and not has_tag(_Diag, None),
       "has_tag 只吃單一 family；連寫或 None ⇒ False")

    # normalize_families / parse_tag_param（SPEC §6.2）
    ck(normalize_families(["R", "a"]) == ("Plover", "Kestrel"), "字母、大小寫不敏感")
    ck(normalize_families(["kestrel", "HERON"]) == ("Kestrel", "Heron"), "全名、大小寫不敏感")
    ck(normalize_families(["m", "R", "Plover"]) == ("Plover", "Heron"), "去重＋恆 FAMILIES 序")
    ck(normalize_families(["zzz", 7, None]) == (), "不認得／非字串一律忽略")
    ck(normalize_families(None) == () and normalize_families([]) == (), "None／空 ⇒ ()")
    ck(normalize_families(f for f in ["R"]) == ("Plover",), "吃 generator")
    try:
        normalize_families("Plover")
        _raised = False
    except TypeError:
        _raised = True
    ck(_raised, "normalize_families 傳 str ⇒ TypeError（逐字元迭代腳槍）")
    ck(parse_tag_param("R,a") == ("Plover", "Kestrel"), "?tag=R,a")
    ck(parse_tag_param("kestrel") == ("Kestrel",), "?tag=kestrel")
    ck(parse_tag_param("m , R") == ("Plover", "Heron"), "空白與逗號混用、回傳恆 FAMILIES 序")
    ck(parse_tag_param("R+M") == ("Plover", "Heron"), "加號也是分隔符")
    ck(parse_tag_param("R,zzz") == ("Plover",), "不認得的字樣忽略")
    ck(parse_tag_param("") == () and parse_tag_param("   ") == () and parse_tag_param(None) == (),
       "空／空白／None ⇒ ()＝沒選＝全部")
    ck(parse_tag_param("zzz") == (), "全不認得 ⇒ ()（呼叫端據此回全部）")
    ck(parse_tag_param(["R"]) == ("Plover",), "非字串就退回 normalize_families")

    pool = [_Diag, _RA, _AO, "EID-99999", dft[0]]
    ck(filter_eids(pool, []) == pool, "空選＝全部、且保留輸入順序")
    ck(filter_eids(pool, None) == pool, "None 一樣＝全部")
    ck(filter_eids(pool, ["Nessie"]) == pool, "全不認得 ⇒ 視同空選＝全部（不回空表）")
    ck(filter_eids(pool, ["Kestrel"]) == [_RA, _AO, "EID-99999", dft[0]],
       "Kestrel 過濾：Plover-only 掉、default 台留（C-PT3）")
    ck(filter_eids(pool, ["A"]) == filter_eids(pool, ["kestrel"]) == filter_eids(pool, ["Kestrel"]),
       "字母／小寫全名／全名三種寫法同結果")
    ck(filter_eids(pool, parse_tag_param("A")) == filter_eids(pool, ["Kestrel"]),
       "parse_tag_param 的輸出可直餵 filter_eids（§6.2 動線）")
    ck(filter_eids(pool, ["Plover", "Heron"]) == [_Diag, _RA, "EID-99999", dft[0]],
       "多選＝OR")
    ck(filter_eids([], ["Plover"]) == [], "空輸入 ⇒ 空輸出")
    ck(filter_eids((e for e in pool), ["Kestrel"]) == filter_eids(pool, ["Kestrel"]),
       "eids 吃 generator（family 集合只算一次）")
    for _bad in (_Diag, "R"):
        try:
            filter_eids(_bad, ["Plover"])
            _raised = False
        except TypeError:
            _raised = True
        ck(_raised, f"filter_eids 的 eids 傳 str ⇒ TypeError（{_bad!r}）")

    # ── 規則邊界（門檻是 OR；分母含 UNKNOWN）──────────────────────────────────
    # ⚠ 只 import 產生器的**純函式**重算（零 MES、零網路）；路徑自解析，不依賴 cwd。
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import _gen_product_tags as _gen
    _cf = _gen.counted_families
    U = UNKNOWN_FAMILY
    ck(_cf({"Plover": 49, U: 9951}) == (), "49 段且 0.49% ⇒ 不計（兩條都沒過）")
    ck(_cf({"Plover": 50, U: 9950}) == ("Plover",), "50 段 ⇒ 計（絕對段數腿）")
    ck(_cf({"Plover": 1, U: 100}) == (), "1/101＝0.99% 且 <50 段 ⇒ 不計（佔比腿差一點）")
    ck(_cf({"Plover": 1, U: 99}) == ("Plover",), "1/100＝1% 且 <50 段 ⇒ 計（佔比腿剛好過）")
    ck(_cf({"Plover": 49, "Kestrel": 51, U: 9900}) == ("Kestrel",), "同線兩 family 各自判")
    ck(_cf({"Kestrel": 60, "Plover": 60}) == ("Plover", "Kestrel"), "回傳依 FAMILIES 序、非輸入序")
    ck(_cf({U: 8000}) == (), "整線只有 UNKNOWN ⇒ 空（C-PT2）")
    ck(_cf({}) == () and _cf({"Plover": 0}) == (), "空線／零段 ⇒ 空、不除零")
    ck(_cf({"Plover": 3, "Kestrel": 1}) == ("Plover", "Kestrel"),
       "小總量線：3/4 與 1/4 都過 1% ⇒ 佔比腿救得起小段數")
    ck(_gen.line_share({"Plover": 1, U: 3}, "Plover") == 0.25
       and _gen.line_share({}, "Plover") == 0.0, "line_share 分母含 UNKNOWN、零段不除零")

    # ── 來源檔完整性（y1／y2 逐站守恆；QA 表不進計算）─────────────────────────
    # 普查檔不存在（例：脫敏公開樹不帶原始普查）⇒ 標 skipped、不算失敗；存在 ⇒ 照舊全比對。
    if not _os.path.exists(_os.path.join(_here, CENSUS_FILE)):
        print("board_tags selftest: skipped: census file absent (%s)" % CENSUS_FILE)
    else:
        _rows, _y1, _y2, _md5, _wend, _stats = _gen.load_census()
        ck(_md5 == CENSUS_MD5, f"普查檔 md5 與本檔釘的一致，實 {_md5}")
        ck(_wend == CENSUS_WINDOW_END, "窗末一致")
        ck(_stats["main_rows"] == _pp.PINS["tags.census_main_rows_n"],
           f"主表列數應與私有釘值相同，實 {_stats['main_rows']}")
        ck(_stats["stations"] == _pp.PINS["tags.census_stations_n"],
           f"QA 表站數應與私有釘值相同，實 {_stats['stations']}")
        ck(_stats["eids"] == _pp.PINS["tags.census_eids_n"], "主表 distinct EID ≡ 私有釘值")
        ck(_stats["fanout_total"] == _pp.PINS["tags.fanout_total_n"]
           and _stats["fanout_stations"] == _pp.PINS["tags.fanout_stations_n"]
           and _stats["fanout_max"] == _pp.PINS["tags.fanout_max_n"],
           f"y2−y1 join 放大形狀應與私有釘值相同，實 {_stats}")
        ck(_gen.build_tags(_rows) == PRODUCT_TAGS, "從普查重算 ≡ 本檔 PRODUCT_TAGS（逐台逐欄）")
        try:
            _gen.check_integrity(_rows[:-1], _y1, _y2)
            _raised = False
        except ValueError:
            _raised = True
        ck(_raised, "抽掉主表一列 ⇒ 逐站守恆閘應 raise（閘是活的、不是裝飾）")
        try:
            _gen.split_blocks("a\tb\n\nc\td")
            _raised = False
        except ValueError:
            _raised = True
        ck(_raised, "block 數不是 3 ⇒ raise")

        # ── 產生器 --check（本檔確實是那份普查生出來的、不是手改過的）─────────────
        ck(_gen.main(["--check"]) == 0, "產生器 --check 應回 0（本檔＝普查的決定性產物、無手改）")

    print(f"board_tags selftest OK ({n[0]} checks)")
    return n[0]


if __name__ == "__main__":
    import argparse

    try:
        for _s in (sys.stdout, sys.stderr):
            if (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
                _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        _obs = sum(1 for r in PRODUCT_TAGS.values() if r["origin"] == "observed")
        print(f"board_tags: {len(PRODUCT_TAGS)} EIDs "
              f"({_obs} observed / {len(PRODUCT_TAGS) - _obs} default) "
              f"from {CENSUS_FILE} (md5 {CENSUS_MD5[:8]}..., window end {CENSUS_WINDOW_END})")
