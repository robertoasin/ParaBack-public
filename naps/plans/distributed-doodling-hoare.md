# sc2_goal の拡張（緩和変数の追加）

## Context

`PbSolver.C` の `cmm_sc2`（Sc2最小化戦略）処理では、UNSAT が返るたびに `failed_assumptions` から
最小ペナルティ `min_penalty` を求め、既存の soft literal 群の係数を減算したうえで、緩和変数
（`@relaxing_constraintN`）を `j = 2..failed_assumptions.size()` の個数分だけ生成している
（[PbSolver.C:2341-2384](PbSolver.C#L2341-L2384)）。

各緩和変数 `rel` は制約には追加されるが、目的関数を表す `sc2_goal`（`Linear*`）には反映されて
いない。`sc2_goal` はソフトリテラルとそのペナルティ係数を並べた配列で、後続の反復
（[PbSolver.C:2100-2106](PbSolver.C#L2100-L2106)）で `hitting_assumptions` を作る際や、
`Lit2sc2goalInd` を介して係数を検索・更新する際に参照される。緩和変数を `sc2_goal` に追加
しないと、次回以降のUNSATコア処理で緩和変数のペナルティが正しく扱われない。

`Linear`（[Linear.h](Linear.h)）は placement new で `sizeof(Linear) + n*(sizeof(Lit)+sizeof(Int))`
ちょうどのバッファに確保されるフレキシブル配列構造で、`orig_size`（private, 容量）と `size`
（public, 現在の要素数）は構築時に必ず一致する。`Int` は `NO_GMP` 未定義時は GMP の `mpz_t` を
内包しうる非POD型のため、容量拡張には生バイトの `realloc`/`memmove` ではなく、既存の
`vec<Lit>`/`vec<Int>` から `Linear` を再構築するコンストラクタ経由の再配置が必要（GMP版でも
Int 各要素はコピーコンストラクタで正しく初期化される）。

ユーザーとの確認の結果、`Linear.h` 自体は変更せず、ループ内では新規に追加する `(rel, min_penalty)`
のペアだけを `vec<Lit>`/`vec<Int>` に集約し、ループを抜けた後に一度だけ、既存の `sc2_goal` の内容と
結合して新しい `Linear` を既存の初期化コード（[PbSolver.C:2059](PbSolver.C#L2059)）と同じパターンで
再配置する方針を採用する。これは既存コードが `goal`/`original_goal` などを分割・再構築する際に
使っている手法（[PbSolver.C:2725-2730](PbSolver.C#L2725-L2730)）と一致し、`Linear.h` への変更を
避けて影響範囲を最小化できる。

旧 `sc2_goal` バッファは今回のケースでは他から参照されないため解放して問題ない。ただし既存コードが
`goal1`/`goal2` の再構築時に `//xfree(goal1), xfree(goal2);` と解放処理をコメントアウトしたまま
残している慣習（[PbSolver.C:2724](PbSolver.C#L2724)）に合わせ、`xfree(sc2_goal)` の呼び出しは
実際には有効化せず、コメントアウトした形でコードに残す（将来的に有効化しやすいように、対象や
呼び出し位置がひと目でわかる形で書く）。

## 実装内容

対象ファイル: `PbSolver.C`（2341〜2384行付近、`cmm_sc2` ブロック）

1. 「Augmenting each relaxation variable and its constraint」ループ（2364〜2375行）の直前に、
   新規追加分だけを集める `vec<Lit> new_rel_ps; vec<Int> new_rel_Cs;` を用意する。

2. ループ内、`rel` を生成し `addConstr` した直後（2374行のコメント位置）で、
   `new_rel_ps.push(rel); new_rel_Cs.push(min_penalty);` を追加する
   （ループ内では `sc2_goal` 自体には一切手を触れない）。

3. ループ終了後（2375行の直後、2377行の `addConstr(ps, Cs, Int(1), 1)` の前）に一度だけ、
   既存の `sc2_goal` の内容（`sc2_goal->size` 個の `(*sc2_goal)[i]` / `(*sc2_goal)(i)`）と
   `new_rel_ps`/`new_rel_Cs` を結合した `vec<Lit>`/`vec<Int>` を作り、
   [PbSolver.C:2059](PbSolver.C#L2059) と同じパターンで新しい `Linear` を再配置する:
   ```cpp
   vec<Lit> sc2_ps; vec<Int> sc2_Cs;
   for (int i = 0; i < sc2_goal->size; i++) {
     sc2_ps.push((*sc2_goal)[i]); sc2_Cs.push((*sc2_goal)(i));
   }
   for (int i = 0; i < new_rel_ps.size(); i++) {
     sc2_ps.push(new_rel_ps[i]); sc2_Cs.push(new_rel_Cs[i]);
   }

   Linear* new_sc2_goal = new (xmalloc<char>(sizeof(Linear) + sc2_ps.size()*(sizeof(Lit) + sizeof(Int))))
     Linear(sc2_ps, sc2_Cs, sc2_goal->lo, sc2_goal->hi, sc2_goal->llt);
   // xfree(sc2_goal);  // 旧バッファは他から参照されないため本来は解放して問題ない
   sc2_goal = new_sc2_goal;
   ```

4. `Lit2sc2goalInd` は既存の初期化コード（[PbSolver.C:2061-2065](PbSolver.C#L2061-L2065)）と
   同じループを再実行して、新しい `sc2_goal` の全要素（既存分＋追加分）についてインデックスを
   張り直す。`vec::growTo(n, fill)` を使う既存パターンをそのまま踏襲する。

## 変更しない箇所

- `Linear.h`（構造体自体は変更しない）
- `Lit2sc2goalInd` の型・宣言（`vec<int>`、[PbSolver.C:2057](PbSolver.C#L2057)）
- 緩和変数の制約生成ロジック（`addConstr` 呼び出し部分）

## 検証方法

1. `make d` でデバッグビルド（`naps_debug`）。
2. `cd build_debug && ctest -V` で既存テストが通ることを確認（Sc2 関連のテストがあれば重点的に確認）。
3. `mt_sc2` 最小化戦略（`-minimization` オプション等で cmm_sc2 が選択されるケース、または
   config 由来で `minimization_strategy == mt_sc2` になる既存のサンプル問題）を用いて、
   UNSATコアが複数回発生する最適化問題（例: `Examples/` 配下の小規模最適化インスタンス）を
   実際に解かせ、クラッシュや assert 失敗（`Wrong model` 例外）が発生しないこと、および
   最終的な最適値が Sc2 を使わない場合（`mt_Sequential` 等）と一致することを確認する。
