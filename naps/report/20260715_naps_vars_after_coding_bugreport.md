# naps バグレポート: coding() 後に getVar した変数が壊れる

- **対象バージョン**: naps **1.4a7**（`vendor/naps-1.4a7-cp312-cp312-linux_x86_64.whl`、Python 3.12.6）
- **報告日**: 2026-07-15
- **依存**: 再現は naps 単体（avs 非依存）。
- **関連**: これは 1.4a7 で修正された assumption 消失バグ（`report/20260714_naps_assumption_bug.md`）とは
  **別問題**。assumption が直ったことで初めて incremental のマルチステップを実測でき、露呈した。

---

## TL;DR

`repeated_optimizations = True` の incremental フローで、**最初の `coding()` より後に `getVar` した
変数は正しく扱われない。** 具体的には:

1. **【不変・100% 再現】** その変数は `best_model` / `index2name` に**現れない**。
   読み出しベクトルが最初の `coding()` 時点の変数数で固定される。
2. **【間欠・未定義動作の兆候】** その変数へ課したハード制約が**実行ごとに効いたり落ちたりする**
   （`get_best_goalvalue()` が期待値 51 と、制約が消えた 1 の間でぶれる）。
3. **回避策（100% 再現）**: 使う変数を**最初の `coding()` の前に全部 `getVar`** しておけば正しく動く
   （制約は `coding()` 後に足しても効く）。

「変数まわりの内部表現が `coding()` 時点で確定し、以降 `getVar` された変数がその配列・モデル表現に
組み込まれない（かつ未初期化のまま参照される）」という仮説と整合する。

## 最小再現

`docs/repro/naps_vars_after_coding_dropped.py`（exit 1。直れば exit 0）。要点だけ抜くと:

```python
import naps
from naps import Lit

cfg = naps.Config(); cfg.verbosity = 0
cfg.repeated_optimizations = True
s = naps.PbSolverMinimize(cfg)

a, b = s.getVar('a'), s.getVar('b')
s.addConstr([Lit(a), Lit(b)], [1, 1], rhs=1, ineq=1)   # a + b >= 1
s.addGoal([Lit(a), Lit(b)], [1, 1])                    # minimize a + b -> 1
s.coding(); s.solve()                                  # goalvalue = 1（正しい）

c = s.getVar('c')                                      # ★ coding() より後に getVar
s.addConstr([Lit(c)], [1], rhs=1, ineq=1)              # ハード制約: c = 1 を強制
s.addGoal([Lit(a), Lit(b), Lit(c)], [1, 1, 50])        # minimize a + b + 50c -> 51
s.convertPbs(0); s.solve()

model = {s.index2name(i): s.best_model[i] for i in range(len(s.best_model))}
print(s.get_best_goalvalue())   # 期待 51。ときどき 1（c>=1 が落ちる）
print('c' in model)             # 期待 True。実際は必ず False
```

### 期待される挙動

- `get_best_goalvalue() == 51`（`a`=1 で 1、強制された `c`=1 で 50）。
- `c` が `best_model` に含まれ、値は `True`。

### 実際の挙動（1.4a7）

- `'c' in model` は **必ず `False`**（後付け変数はモデルに載らない）。20 回試して 0/20。
- `get_best_goalvalue()` は主に **51**（＝制約は内部的には効いている）だが、
  **cold-start のプロセス初回で稀に 1**（＝ハード制約 `c>=1` が黙って落ちる）になる。
  実測: 別命名（`y0,y1,z`）の初回実行、および `a,b,c` を 8 プロセス回したうちの 1 回目で `1` を観測。
  同一プロセス内で 20 連続だと毎回 51 → **初期化されていない内部状態を参照している疑い**。

### 回避策（確実に動く）

```python
a, b = s.getVar('a'), s.getVar('b')
c = s.getVar('c')          # ★ coding() の前に登録しておく（制約はまだ足さない）
...
s.coding(); s.solve()
s.addConstr([Lit(c)], [1], rhs=1, ineq=1)   # 後から足しても効く
s.addGoal([Lit(a), Lit(b), Lit(c)], [1, 1, 50]); s.convertPbs(0); s.solve()
# -> goalvalue=51、c は best_model に True で載る（毎回）
```

## より複雑な incremental 使用での追加症状（avs Phase 3）

条件付き制約（`llt`/`rlt`）と assumption を含む本番相当の incremental プロトタイプ
（`bench/incremental_probe.py`）では、同じ根に由来すると思われる次も観測した:

- **`get_best_goalvalue()` がモデルの実目的値と大きく食い違う。** 窓を広げた step1 で naps 内部値
  6157480 に対し、返った `best_model` を目的関数に代入し直すと 242895（＝ step0 の解のまま）。
  status は `best`（最適性を主張）。新変数と活性化リテラル `act` が coding 後に getVar されるため、
  solver は step0 の問題を解き直しているだけになる。
- **新変数を取り込ませようと `coding()` を再度呼ぶと `no such variable index` で落ちる**
  （最小例では通るが、条件付き制約＋補助変数を含む実問題で再現）。

## 影響と優先度

- avs の Phase 3（incremental 求解）はこの一点でブロックされている。回避策 A（全変数の事前登録）は
  「全曲の変数空間を最初の `coding()` に前倒しする」ことになり、incremental の狙い（積み直しコスト
  削減・学習節再利用）と相反するため、性能面で採る価値が疑わしい。
- ユーザー判断（2026-07-15）: naps 側で `getVar` 後の変数を `convertPbs`/モデル表現に正しく取り込む
  には変数まわりの**大規模リファクタが要り短期では直らない**。avs 側は当面 Phase 3 をパークし、
  作り捨て経路（`--solver naps-py`、フル曲 6 ステップ 135.4s）を維持する。

## チェックリスト（修正後の確認）

- [ ] `docs/repro/naps_vars_after_coding_dropped.py` が exit 0（`c` が毎回モデルに載り、goalvalue=51 安定）。
- [ ] `bench/incremental_probe.py 3 example/ballade1/ballade1-106-108.mid` の incremental が
      baseline と全ステップ一致（step0=242895, step1=251500, step2=267563）。
- [ ] `coding()` の再呼び出しが `no such variable index` を出さない。
