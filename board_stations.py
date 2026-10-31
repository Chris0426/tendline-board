"""Tendline Board 共用站別流序（`SPEC-LANDING.md` §18.20；C-LD34–C-LD36）。

唯一流序由 Qlik PLOVER & HERON／KESTREL 兩張表合成；本模組只含常數與純函式，
零 I/O、零 app 依賴。C-LD34 禁止消費端另抄站序；C-LD35 要求未知站穩定沉底；
C-LD36 要求兩源共同站的相對序有矛盾時立刻 raise。
"""

import mes_qlik


def merge_flow(plover_cls, kestrel_cls) -> tuple:
    """以共同站為錨合成兩條 Qlik 流序；相對序矛盾即 `ValueError`。

    每個錨與下一個錨之間，先接 Plover 獨有站、再接 Kestrel 獨有站；前後兩端
    沒有錨的缺口也套同一規則。來源若有重複 cls，同樣無法產生單一無重複流序，直接炸。
    """
    plover = tuple(plover_cls)
    kestrel = tuple(kestrel_cls)
    plover_set, kestrel_set = set(plover), set(kestrel)
    if len(plover_set) != len(plover) or len(kestrel_set) != len(kestrel):
        raise ValueError("station source contains duplicate cls")

    plover_anchors = tuple(cls for cls in plover if cls in kestrel_set)
    kestrel_anchors = tuple(cls for cls in kestrel if cls in plover_set)
    if plover_anchors != kestrel_anchors:
        raise ValueError("shared station order conflicts between Plover and Kestrel")

    out = []
    plover_at = kestrel_at = 0
    for anchor in plover_anchors:
        plover_next = plover.index(anchor, plover_at)
        kestrel_next = kestrel.index(anchor, kestrel_at)
        out.extend(plover[plover_at:plover_next])
        out.extend(kestrel[kestrel_at:kestrel_next])
        out.append(anchor)
        plover_at, kestrel_at = plover_next + 1, kestrel_next + 1
    out.extend(plover[plover_at:])
    out.extend(kestrel[kestrel_at:])
    return tuple(out)


QLIK_FLOW_ORDER = merge_flow(
    tuple(c for _o, c, _m in mes_qlik.QLIK_STATIONS),
    tuple(c for _o, c, _m in mes_qlik.KESTREL_STATIONS),
)
FLOW_RANK: dict[str, int] = {cls: rank for rank, cls in enumerate(QLIK_FLOW_ORDER)}


def flow_rank(cls) -> int:
    """回傳 0-based 流序 rank；未知站回尾端 rank（C-LD35：沉底、不丟列）。"""
    return FLOW_RANK.get(cls, len(QLIK_FLOW_ORDER))


def flow_sorted(rows, key=lambda r: r["cls"]):
    """依 Qlik 流序穩定排序；未知站保原相對序沉底。"""
    return sorted(rows, key=lambda r: flow_rank(key(r)))


def _selftest():
    """純離線合成自測；不讀檔、不連 MES。"""
    n = 0

    def ck(cond, label):
        nonlocal n
        n += 1
        if not cond:
            raise AssertionError(label)

    plover = tuple(c for _o, c, _m in mes_qlik.QLIK_STATIONS)
    kestrel = tuple(c for _o, c, _m in mes_qlik.KESTREL_STATIONS)
    plover_set, kestrel_set = set(plover), set(kestrel)

    ck(tuple(c for c in QLIK_FLOW_ORDER if c in plover_set) == plover,
       "QLIK_FLOW_ORDER 投影到 Plover 必須等於 QLIK_STATIONS")
    ck(tuple(c for c in QLIK_FLOW_ORDER if c in kestrel_set) == kestrel,
       "QLIK_FLOW_ORDER 投影到 Kestrel 必須等於 KESTREL_STATIONS")
    ck(len(QLIK_FLOW_ORDER) == 55 and len(set(QLIK_FLOW_ORDER)) == len(QLIK_FLOW_ORDER),
       "QLIK_FLOW_ORDER 必須是 55 個無重複 cls")

    conflicted = list(kestrel)
    conflicted[0], conflicted[1] = conflicted[1], conflicted[0]
    try:
        merge_flow(plover, conflicted)
    except ValueError:
        ck(True, "共同站相對序矛盾必須 raise")
    else:
        ck(False, "共同站相對序矛盾未 raise")

    sample = [{"cls": "unknown-first"}, {"cls": plover[1]},
              {"cls": "unknown-second"}, {"cls": plover[0]}]
    ck([r["cls"] for r in flow_sorted(sample)]
       == [plover[0], plover[1], "unknown-first", "unknown-second"],
       "未知站必須穩定沉底")
    ck([r["cls"] for r in flow_sorted([{"cls": cls} for cls in plover])] == list(plover),
       "flow_sorted 對 QLIK_STATIONS cls 序必須是恆等")
    print(f"[board_stations] selftest PASS — {n} 項斷言")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        print("usage: python board_stations.py --selftest")
        raise SystemExit(2)
