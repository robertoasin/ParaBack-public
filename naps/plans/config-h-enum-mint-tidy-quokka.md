# Config.h: MinT に mt_sc2 を追加し、-sc2 オプションとして解析

## Context

`enum MinT`（[Config.h:13](Config.h#L13)）は最小化戦略の種類を表し、[Main.C:361-365](Main.C#L361-L365) でコマンドラインオプション文字列（`seq`, `bin`, `alt,seq-bin`, `hitting`, `seq-hitting`）と対応付けられている。今回、新たな最小化戦略 `mt_sc2` を追加し、`-sc2` フラグで選択できるようにする。

## 変更内容

### 1. [Config.h:13](Config.h#L13)
`enum MinT` に `mt_sc2` を追加する:

```cpp
enum MinT     { mt_Sequential, mt_Binary, mt_Hitting, mt_Sequential_Binary, mt_Sequential_Hitting, mt_sc2};
```

### 2. [Main.C:365](Main.C#L365) 付近
既存の並びに倣い、`-sc2` オプションを追加する:

```cpp
else if (oneof(arg, "sc2"         )) config->minimization_strategy = mt_sc2;
```

（`seq-hitting` の行の直後に追加）

## 備考

- `mt_sc2` の具体的な最小化ロジックは今回のスコープに含まれない。[PbSolver.C:1825](PbSolver.C#L1825) の `switch(config->minimization_strategy)` 文には現時点では `case mt_sc2:` を追加しない（ユーザーから明示的な指示があれば別途対応）。ロジック未実装のまま `-sc2` を指定した場合、switch文の default 分岐の挙動に従う（要確認、実装時に switch 文の default ケースを確認する）。
- 既存パターン（enum 追加 + oneof での文字列マッピング）をそのまま踏襲するのみで、他のリファクタリングは行わない。

## 検証

- `make d` でデバッグビルドが通ることを確認する。
- `./naps_debug -sc2 Examples/xxx.opb` 等で起動時にエラーなく `minimization_strategy` が `mt_sc2` に設定されることを確認する（必要なら一時的なデバッグ出力、または既存の verbosity 出力で確認）。
