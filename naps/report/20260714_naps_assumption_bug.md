# 引き継ぎ資料 — naps: `repeated_optimizations` のとき assumption が失われる（+ `Sc2` の segfault）

作成日: 2026-07-14
対象: naps（`~/proj/naps`、`git.trs.css.i.nagoya-u.ac.jp/naps/naps`）1.04a6 / wheel `naps-1.4a6`
報告元: avs（voice separation）Phase 3（incremental 求解）の実装中に発覚
再現例題: [`docs/repro/naps_assumption_ignored.py`](../docs/repro/naps_assumption_ignored.py)（naps 単体で完結、avs 非依存）

1.4a6 で **メモリリークは解消**し、**`mt_Sc2` も Python から選べるようになった**（確認済み）。
その先に進んだところで、別のバグを 2 つ踏んだ。

---

## バグ A — `repeated_optimizations = True` だと `solve(assumps)` の assumption が失われる

### 症状

`cfg.repeated_optimizations = True` のとき、`solve([a])` に渡した assumption `a` が最小化で守られず、
**モデルの中で `a` が偽になる**。`a` を `llt` に持つ制約が空虚に真になり、最適値が緩む。

**初回の `solve()` から再現する**（2 回目の再求解を待つ必要はない）。

### 最小形

```python
cfg.repeated_optimizations = True
a  => x0 + x1 + x2 >= 2        # llt = a
min 1*x0 + 2*x1 + 2*x2
solve([a])

期待: 最適値 3（x0 と、x1 か x2）、model[a] = True
実際: 最適値 0、model[a] = False
```

### 再現に必要な 3 条件（どれか 1 つでも欠けると正しく動く）

| # | 条件 | 欠けたときの挙動 |
|---|---|---|
| 1 | `cfg.repeated_optimizations = True` | `False` なら全戦略で正しく動く |
| 2 | **assumption リテラルを制約の `llt` に使う** | `llt` を使わず係数で書けば正しく動く |
| 3 | **目的関数の係数が不揃い** | 係数が一様（`[1,1,1]`）だと正しく動く |

`docs/repro/naps_assumption_ignored.py` の実測（naps 1.04a6）:

```
 repeated    llt      goal 係数      最適値  model[a]
    False   True    [1, 2, 2]        3      True
     True   True    [1, 1, 1]        2      True
     True   True    [1, 2, 2]        0     False  ← ここだけ落ちる
     True  False    [1, 2, 2]        3      True
```

`minimization_strategy` を変えても再現する（`Sequential_Binary` / `Binary` / `Sequential` /
`Sequential_Sc2` / `Hitting` / `Sequential_Hitting` すべて。`Sc2` はバグ B で落ちる）。

### 切り分けで分かったこと

- **assumption と無関係な `llt` 制約が別にあるだけでは再現しない。**
  再現するのは **assumption リテラルそのものを `llt` に使ったとき**。
- **`a2 <=> a` を挟んで `llt = a2` にしても再現する。** 等価な変数への迂回では避けられない。
- **assumption ではなく単位制約（`a = 1`）で固定すると正しい最適値が出る。**
  avs の実問題（`ballade1-106-108.mid` の step0）で確認: assumption だと 225354（緩む）、
  単位制約で固定すると **242895 = 従来経路と完全一致**。
  → **制約の組み方は正しく、assumption だけが失われている。**

### なぜ avs が困るのか（Phase 3 のブロッカー）

avs の Phase 3 は、スライディングウィンドウを 1 個の solver で解き続ける設計になる
（[plans/20260714_phase3_incremental.md](../plans/20260714_phase3_incremental.md)）。

窓を伸ばすと**書き換わる制約がある**（イベントに辺が増えると、そのイベントの
入辺 `<= 1` / 出辺 `<= 1` / フロー保存 `= 0` の項が増える）。積みっぱなしにはできないので、

- **確定した制約**（もう変わらない）→ 恒久的に `addConstr`
- **未確定の制約** → **活性化リテラル `a_k` 付きで暫定的に `addConstr`（`llt = a_k`）し、
  `solve([a_k])` で有効化。次のステップで `a_k = 0` に固定して殺す**

とする必要がある。これは**バグ A の 3 条件をすべて満たす**:

1. solver を使い回すので `repeated_optimizations = True` が要る
2. 活性化リテラルを `llt` に使う（これが撤回の唯一の手段）
3. 目的関数は辺の重みなので**当然に不揃い**

**単位制約で固定する回避は使えない**（撤回できないので、次のステップで古い制約を殺せない）。
`Sc2` は assumption を守るが、avs の問題では**桁違いに遅い**（step0 で既定の 80 倍、1727s）ので
実用にならない。**バグ A が直るまで Phase 3 は実装できない。**

---

## バグ B — `Sc2` + `repeated_optimizations` + `solve(assumps)` で segfault

`minimization_strategy = Sc2` かつ `cfg.repeated_optimizations = True` のとき、
**`solve(assumps)` を呼ぶと segfault（SIGSEGV）**。決定的に落ちる。

| `llt` | assumption | `repeated_optimizations` | 結果 |
|---|---|---|---|
| あり | あり | **True** | **segfault** |
| なし | あり | **True** | **segfault** |
| あり | あり | False | 正常（最適値 3） |
| あり | なし | True | 正常 |

**引き金は `llt` ではなく「assumption を渡すこと」**。assumption を渡さなければ落ちない。
`Sequential_Sc2` は落ちない（バグ A のほうに該当する）。

---

## 直ったことの確認方法

```sh
python docs/repro/naps_assumption_ignored.py
```

- 現状: バグ A・B の両方を検出して `FAIL`（exit 1）。
- 修正後: `OK`（exit 0）。

`Sc2` の segfault はプロセスごと落ちるので、再現例題ではサブプロセスに隔離してある
（例題自体は落ちない）。

avs 側の最終確認は Phase 3 のプロトタイプ（`bench/incremental_probe.py`）で、
`ballade1-106-108.mid` のゴールデン値 **step0 = 242895 / フル求解 = 267563** と一致すること。

---

## 細かい点（優先度低）

`get_best_goalvalue()` が返す最適値とは別に、**`o <最適値>` の行が stdout に出ることがある**
（`cfg.verbosity = 0` にしていても出る）。C 側で生ファイル記述子に書いているらしく、
Python 側から抑制できない。再現例題の出力の末尾に `o 3` が数行混ざるのはこれ。

---

## 参考: 1.4a6 で確認できたこと（こちらは問題なし）

- **メモリリークは解消**。`docs/repro/naps_pbsolver_memory_leak.py` が exit 0
  （RSS が 18MB で頭打ち。修正前はフル曲で 23GB）。
  フル曲を実際に流しても RSS は 500MB 台で安定している。
- **`mt_Sc2` / `mt_Sequential_Sc2` が Python から選べるようになった。**
  ただし avs の問題では**桁違いに遅い**（フル曲 step0: 既定 `Sequential_Binary` 21.6s に対し
  `Sc2` は **1727s**）。戦略には問題ごとの得手不得手があるので、これは naps の欠陥ではない。
- avs の既定は **`Binary`** に決めた（フル曲の先頭 6 ステップ通しで 156.5s → 135.4s、最適値は不変）。
