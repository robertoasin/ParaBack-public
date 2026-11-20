"""PbSolver を作り捨ててもメモリが解放されることの回帰テスト。

report/20260714_naps_memory_leak.md 参照。修正前は 1 個あたり約 1.2 MB 漏れており、
avs のようにソルバを繰り返し作り捨てる利用（スライディングウィンドウ）で RSS が
数十 GB まで膨らんだ。CLI は exit() で終了しデストラクタを通らないため顕在化しない。
"""
import gc
import os

import pytest

import naps
from naps import Config, Lit

WARMUP = 3          # アロケータが安定するまで捨てる回数
ROUNDS = 6
N_VARS = 2000       # リーク量は問題サイズに比例する
N_CONSTRS = 4000
MAX_GROWTH_MB = 0.5   # 修正前の実測は 1.2 MB/回


def rss_mb():
    with open(f"/proc/{os.getpid()}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS not found")


def build_and_solve():
    """それなりの大きさの PB 問題を組んで解き、solver を捨てる。"""
    cfg = Config()
    cfg.verbosity = 0
    solver = naps.PbSolverMinimize(cfg)

    xs = [solver.getVar(f"x{i}") for i in range(N_VARS)]
    for k in range(N_CONSTRS):
        i, j = k % N_VARS, (k * 7 + 3) % N_VARS
        if i != j:
            # x_i + x_j <= 1
            solver.addConstr([Lit(xs[i]), Lit(xs[j])], [1, 1], rhs=1, ineq=-1)
    solver.addGoal([Lit(x) for x in xs], [1] * N_VARS)

    solver.coding()
    solver.solve()

    del solver
    gc.collect()


@pytest.mark.skipif(not os.path.exists("/proc/self/status"), reason="VmRSS is Linux only")
def test_pb_solver_minimize_does_not_leak():
    for _ in range(WARMUP):
        build_and_solve()

    base = rss_mb()
    for _ in range(ROUNDS):
        build_and_solve()
    growth = (rss_mb() - base) / ROUNDS

    assert growth < MAX_GROWTH_MB, (
        f"PbSolverMinimize を破棄しても 1 個あたり {growth:.2f} MB 解放されていない"
    )
