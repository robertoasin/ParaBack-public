# naps アーキテクチャ

Pseudo-Boolean solver naps と scip-naps の内部構造の解説。
ビルド・テスト・実行方法は [.claude/CLAUDE.md](../.claude/CLAUDE.md) を参照。

## 処理フロー

```
Main.C (エントリポイント・CLIオプション解析)
    ↓
PbSolver / PbSolver_Minimize / PbSolver_Counting  (PbSolver.h/C)
    ↓
PbParser.h/C (OPB・DIMACS パース)
    ↓
PbSolver_convert.C (エンコーディング戦略の選択・制御)
    ├─ PbSolver_convertBdd.C   (BDDエンコーディング)
    ├─ PbSolver_convertSort.C  (ソーティングネットワーク)
    └─ PbSolver_convertAdd.C   (リプルキャリー加算器)
    ↓
Hardware_clausify.C / FEnv.h (CNF節への変換)
    ↓
Solver.h/C (SATソルバ抽象レイヤー)
    ├─ CaDiCaL 3  (最適化問題のデフォルト)
    ├─ Kissat 4   (充足可能性判定のデフォルト)
    └─ GlueMiniSat 2.2.6
```

## クラス階層

- `PbSolver` (基底): 制約の格納・正規化・伝播
- `PbSolver_Minimize`: 目的関数付き最適化（逐次/2分探索/ヒッティングセット）
- `PbSolver_Counting`: 全解列挙

## ソースファイルマップ

| ファイル | 行数(目安) | 役割 |
|---|---|---|
| `PbSolver.C` / `.h` | 3500 / 490 | ソルバ中核。制約格納・正規化・伝播、PbSolver_Minimize（多段最適化）、PbSolver_Counting（全解列挙） |
| `PbSolver_scip.C` | 1060 | SCIP 連携（LP 緩和・presolve・協調モード・線形制約追加） |
| `Solver.C` / `.h` | 820 / 440 | SATソルバ抽象レイヤ（CaDiCaL/Kissat/GlueMiniSat の切替） |
| `Main.C` / `.h` | 750 / 150 | エントリポイント・CLI オプション解析 |
| `PbSolver_convertBdd.C` | 740 | BDD エンコーディング |
| `PbParser.C` / `.h` | 630 / 45 | OPB / DIMACS パーサ |
| `OptimalBase.C` / `.h` | 560 / 80 | 混合基数（optimal base）計算 |
| `PbSolver_convertSort.C` | 450 | ソーティングネットワーク エンコーディング |
| `Hardware_clausify.C` | 380 | 回路 → CNF 節変換 |
| `PbSolver_convert.C` | 250 | エンコード戦略の選択・制御 |
| `Debug.C` / `.h` | 210 / 65 | デバッグ出力補助 |
| `Hardware_adders.C` | 145 | リプルキャリー加算器 |
| `PbSolver_convertAdd.C` | 120 | 加算器エンコーディング |
| `Hardware_sorters.C` | 110 | ソータ回路構築 |
| `Report.C` / `.h` | 85 / 40 | 出力レポート（reportf 等） |
| `Config.h` | 190 | enum SolverT / ConvertT / Command / MinT 等の設定定義 |
| `Primes.h` | 210 | PB 競技用素数表 |
| `Linear.h` | 35 | PB 制約クラス |
| `Version.h` | 4 | バージョン文字列 |
| `ADTs/` | - | 基本データ構造（FEnv, File, Global 等） |

## PbSolver_Minimize::solve のマルチステージ最適化（重要）

`divideGoal` によって元の目的関数が複数のサブゴール `goals[0], goals[1], ..., goals[K-1]` に
分解される場合、以下の**辞書式最適化**を行う：

1. **stage 1**: `goals[0]` を最大化 → 最適値 V0 を確定
2. **stage 2**: 制約 `goals[0] >= V0`（＝ `goals[0] = V0`）を追加したうえで `goals[1]` を最大化 → V1 を確定
3. **stage N**: 前ステージまでの全制約 `goals[i] = Vi (i < N)` を追加したうえで `goals[N]` を最大化

**アルゴリズム上の重要ポイント**：

- stage N で確定した `goals[i] = Vi (i < N)` の等式制約は、後続 stage でも**常に有効**である。
  後続 stage の実行可能領域は `goals[i] = Vi` を満たす解の集合であり、`goals[i]` がその
  最大値 Vi を超えることは不可能なため、等式制約は LP 緩和においても矛盾しない。
- cooperative SCIP モードで `addScipLinearConstraint` により SCIP に等式制約を追加する際も、
  `lb = ub = Vi` として渡すことが正しく、この制約は stage N+1 以降の SCIP の LP 緩和に
  悪影響を与えない（むしろ探索空間を正確に絞り込む）。
- `solveScip` 内での SCIP グローバル境界の SAT 伝播（B&B ベース）は非最終ステージでは
  無効化されている（`false &&`）。代わりに `addScipLinearConstraint`（ステージ境界）で
  `SCIPpresolve` を用いた制約伝播ベースの正確な変数固定を行う（`-scip-stage-prop` オプション）。

## エンコーディング戦略（`-ct` オプション）

| 戦略               | 特性                           |
|--------------------|--------------------------------|
| BDD                | 係数が小さい・有界ドメイン向き |
| Sorters            | カーディナリティ制約向き       |
| Adders             | 係数が大きい算術制約向き       |
| Mixed (デフォルト) | 制約ごとに自動選択             |

## 最小化戦略（`-minimization` オプション）

- `mt_Sequential`: 上界を単調に縮小
- `mt_Binary`: 二分探索
- `mt_Hitting`: ヒッティングセット（UNSAT コア活用）
- `mt_Sequential_Binary`: ハイブリッド（デフォルト: 0）
