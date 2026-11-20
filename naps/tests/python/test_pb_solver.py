# tests/pb_solver_test.cpp の Python 移植。
# 各テストは gtest の TEST(PB_solver_test, ...) と 1:1 に対応し、同じ制約・同じ期待値を検証する。
# report->reportf(...) によるログ出力は print(...) に置き換えている。

import pytest

import naps
from naps import Command, Config, Lit, MinT, SolverStatus, SolverT

needs_scip = pytest.mark.skipif(not naps.HAS_SCIP, reason="built without SCIP")


# ---------------------------------------------------------------- helpers (C++ 版と同名)

def output(solver):
    status = solver.get_pb_solver_status()
    if status == SolverStatus.best:
        print(f"OPT: {solver.get_best_goalvalue()}, ", end="")
    if status == SolverStatus.sat:
        print("SAT: ", end="")
    if status in (SolverStatus.sat, SolverStatus.best):
        model = solver.best_model
        parts = []
        for x in range(len(model)):
            name = solver.index2name(x)
            if not name.startswith("@"):
                parts.append(("" if model[x] else "-") + name)
        print(" ".join(parts))
    elif status == SolverStatus.all:
        print(f"ALL MODELS: {solver.get_number_models()}")
    elif status == SolverStatus.unsat:
        print("UNSAT")


def sat_case(solver):
    # 2 x1 + 3 x2 + 6 x3 <= 7
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2")), Lit(solver.getVar("x3"))]
    add = solver.addConstr(ps, [2, 3, 6], rhs=7, ineq=-1, rhs2=None, ineq2=-2)
    print(f"2 x1 3 x2 6 x3 <= 7;\naddConstr: {add}")

    # 2 x1 + 3 x2 + 6 x3 >= 4
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2")), Lit(solver.getVar("x3"))]
    add = solver.addConstr(ps, [2, 3, 6], rhs=4, ineq=1, rhs2=None, ineq2=-2)
    print(f"2 x1 3 x2 6 x3 >= 4;\naddConstr: {add}")


def set_goal_case(solver):
    # goal: 1 x1 - 1 x2 - 1 x3
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2")), Lit(solver.getVar("x3"))]
    solver.addGoal(ps, [1, -1, -1])
    print("goal: 1 x1 -1 x2 -1 x3\naddGoal: ")


def min_multistage_case(solver):
    # 1 x1 + 1 x2 + 4 x3 + 4 x4 + 4 x5 >= 5
    ps = [Lit(solver.getVar(n)) for n in ("x1", "x2", "x3", "x4", "x5")]
    add = solver.addConstr(ps, [1, 1, 4, 4, 4], rhs=5, ineq=1, rhs2=None, ineq2=-2)
    print(f"addConstr: {add}")

    # goal: 1 x1 + 1 x2 + 10004 x3 + 10004 x4 + 20002 x5
    ps = [Lit(solver.getVar(n)) for n in ("x1", "x2", "x3", "x4", "x5")]
    solver.addGoal(ps, [1, 1, 10004, 10004, 20002])
    print("addGoal: ")


def unsat_case(solver):
    # 1 x1 + 2 x2 <= 1
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2"))]
    add = solver.addConstr(ps, [1, 2], rhs=1, ineq=-1, rhs2=None, ineq2=-2)
    print(f"added: 1 * x1 + 2 * x2 <= 1\nresult: {add}")

    # 1 x1 + 2 x2 >= 2
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2"))]
    add = solver.addConstr(ps, [1, 2], rhs=2, ineq=1, rhs2=None, ineq2=-2)
    print(f"added: 1 * x1 + 2 * x2 >= 2\nresult: {add}")


def set_extraGoal_for_garden9x9(solver):
    # 1 x1 - 1 x2 - 1 x3
    ps = [Lit(solver.getVar("x1")), Lit(solver.getVar("x2")), Lit(solver.getVar("x3"))]
    solver.addGoal(ps, [1, -1, -1])
    print("addGoal: ")


# ---------------------------------------------------------------- tests

def test_sat():
    solver1 = naps.PbSolver()
    solver1.report.setReportFlag(0, True)

    sat_case(solver1)

    solver1.coding()
    solver1.solve()
    print(f"status: {solver1.get_pb_solver_status()}")
    assert solver1.get_pb_solver_status() == SolverStatus.sat
    output(solver1)


def test_unsat():
    solver1 = naps.PbSolver()
    solver1.report.setReportFlag(0, True)

    unsat_case(solver1)

    solver1.coding()
    solver1.solve()
    print(f"status: {solver1.get_pb_solver_status()}")
    assert solver1.get_pb_solver_status() == SolverStatus.unsat
    output(solver1)


@needs_scip
def test_scip_sat():
    config = Config()
    config.verbosity = 3
    config.use_scip = True

    solver1 = naps.PbSolver(config)
    solver1.report.setReportFlag(0, True)

    sat_case(solver1)

    solver1.coding()
    solver1.solve()
    assert solver1.get_pb_solver_status() == SolverStatus.sat
    output(solver1)


@needs_scip
def test_scip_unsat():
    config = Config()
    config.verbosity = 1
    config.use_scip = True

    solver1 = naps.PbSolver(config)
    solver1.report.setReportFlag(0, True)

    unsat_case(solver1)

    solver1.coding()
    solver1.solve()
    assert solver1.get_pb_solver_status() == SolverStatus.unsat


def test_empty_constraint():
    config = Config()

    solver = naps.PbSolverMinimize(config)
    set_goal_case(solver)

    solver.coding()
    solver.solve()

    output(solver)
    assert solver.get_best_goalvalue() == -2


def test_incremental():
    config = Config()
    config.repeated_optimizations = True

    solver = naps.PbSolverMinimize(config)

    x1 = solver.getVar("x1")
    x2 = solver.getVar("x2")
    x3 = solver.getVar("x3")
    ps = [Lit(x1, False), Lit(x2, False), Lit(x3, False)]
    Cg = [-2, -1, -2]
    Cs = [1, 1, 2]

    # goal: 2 * x1 + 1 * x2 + 2 * x3
    solver.addGoal(ps, Cg)

    # 1 * x1 + 1 * x2 + 2 * x3 <= 3
    add = solver.addConstr(ps, Cs, rhs=3, ineq=-1, rhs2=None, ineq2=-2)
    assert add is True
    solver.coding()
    solver.solve()
    assert solver.get_pb_solver_status() == SolverStatus.best
    assert solver.best_model[0] == 1
    assert solver.best_model[1] == 0
    assert solver.best_model[2] == 1
    assert solver.get_best_goalvalue() == -4

    # 1 * x1 + 1 * x2 + 2 * x3 <= 2
    add = solver.addConstr(ps, Cs, rhs=2, ineq=-1, rhs2=None, ineq2=-2)
    solver.convertPbs(0)
    solver.solve()
    assert solver.get_pb_solver_status() == SolverStatus.best
    assert solver.best_model[0] == 1
    assert solver.best_model[1] == 1
    assert solver.best_model[2] == 0
    assert solver.get_best_goalvalue() == -3


# --- report/20260715_naps_vars_after_coding_bugreport.md の回帰テスト ---
# 最初の coding() より後に getVar した変数が best_model / index2name に載り、
# その変数へのハード制約が決定的に反映されることを検証する。

def _incremental_new_var_run(predeclare, recode=False):
    """a+b>=1 を解いた後、c=1 を強制し 50c を goal に足して再 solve。期待: goalvalue=51, c=True。"""
    config = Config()
    config.repeated_optimizations = True
    solver = naps.PbSolverMinimize(config)

    a, b = solver.getVar("a"), solver.getVar("b")
    if predeclare:
        c = solver.getVar("c")                       # 回避策: coding 前に登録
    assert solver.addConstr([Lit(a), Lit(b)], [1, 1], rhs=1, ineq=1) is True
    solver.addGoal([Lit(a), Lit(b)], [1, 1])
    solver.coding()
    solver.solve()
    assert solver.get_pb_solver_status() == SolverStatus.best
    assert solver.get_best_goalvalue() == 1

    if not predeclare:
        c = solver.getVar("c")                       # バグ条件: coding 後に登録
    assert solver.addConstr([Lit(c)], [1], rhs=1, ineq=1) is True
    solver.addGoal([Lit(a), Lit(b), Lit(c)], [1, 1, 50])
    if recode:
        solver.coding()                              # 再 coding が例外を出さないこと
    else:
        solver.convertPbs(0)
    solver.solve()

    model = solver.best_model
    named = {solver.index2name(i): model[i] for i in range(len(model))}
    return solver.get_best_goalvalue(), named


def test_incremental_var_after_coding():
    # 旧コードは best_model の範囲外読み（未初期化ヒープ）で非決定だったため反復して検証する
    for _ in range(10):
        goalvalue, named = _incremental_new_var_run(predeclare=False)
        assert goalvalue == 51
        assert named.get("c") is True
        assert named.get("a", False) + named.get("b", False) == 1


def test_incremental_var_before_coding():
    # 回避策（coding 前に全変数を登録）が引き続き動くこと
    goalvalue, named = _incremental_new_var_run(predeclare=True)
    assert goalvalue == 51
    assert named.get("c") is True


def test_recoding_after_new_var():
    # coding() の再呼び出しが "no such variable index" 等を出さないこと（チェックリスト3）
    goalvalue, named = _incremental_new_var_run(predeclare=False, recode=True)
    assert goalvalue == 51
    assert named.get("c") is True


@needs_scip
def test_scip_incremental():
    config = Config()
    config.repeated_optimizations = True
    config.use_scip = True

    solver = naps.PbSolverMinimize(config)

    x1 = solver.getVar("x1")
    x2 = solver.getVar("x2")
    x3 = solver.getVar("x3")
    ps = [Lit(x1, False), Lit(x2, False), Lit(x3, False)]
    Cg = [2, 1, 2]
    Cs = [1, 1, 2]

    solver.addGoal(ps, Cg)

    add = solver.addConstr(ps, Cs, rhs=3, ineq=-1, rhs2=None, ineq2=-2)
    assert add is True
    solver.coding()
    solver.solve()
    assert solver.get_pb_solver_status() == SolverStatus.best

    for v in solver.best_model:
        assert v == 0

    add = solver.addConstr(ps, Cs, rhs=2, ineq=1, rhs2=None, ineq2=-2)
    solver.solve()
    assert solver.get_pb_solver_status() == SolverStatus.best
    assert solver.best_model[0] == 0
    assert solver.best_model[1] == 0
    assert solver.best_model[2] == 1
    assert solver.get_best_goalvalue() == 2


def test_external_solver_injection():
    config = Config()
    config.solver = SolverT.CaDiCaL

    # SAT ソルバを外部で生成して注入する
    test_report = naps.Report()
    config.report = test_report
    sat_solver = naps.Solver_invoke(SolverT.CaDiCaL, config)

    pb_solver = naps.PbSolver(config, sat_solver)

    # config が注入したものと同一であることを確認
    assert pb_solver.config is config

    # 1 * x1 >= 1
    ps = [Lit(pb_solver.getVar("x1"))]
    pb_solver.addConstr(ps, [1], rhs=1, ineq=1, rhs2=None, ineq2=-2)

    pb_solver.coding()
    pb_solver.solve()
    assert pb_solver.get_pb_solver_status() == SolverStatus.sat


def test_assumption():
    config = Config()
    config.verbosity = 2
    solver = naps.PbSolver(config)

    y1 = solver.getVar("y1")
    y2 = solver.getVar("y2")
    y3 = solver.getVar("y3")
    y4 = solver.getVar("y4")

    ps = [Lit(y1), Lit(y2), Lit(y3), Lit(y4)]
    Cs = [1, 1, 2, 2]

    # 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 <= 4
    add = solver.addConstr(ps, Cs, rhs=4, ineq=-1, rhs2=None, ineq2=-2)
    print(f"added: 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 <= 4\nresult: {add}")

    # 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 >= 3
    add = solver.addConstr(ps, Cs, rhs=3, ineq=1, rhs2=None, ineq2=-2)
    print(f"added: 1 * y1 + 1 * y2 + 2 * y3 + 2 * y4 >= 3\nresult: {add}")

    solver.coding()

    ly1, ly2, ly3, ly4 = Lit(y1, True), Lit(y2, True), Lit(y3, True), Lit(y4, True)

    solver.solve([ly1, ly2, ly3, ly4])
    assert solver.get_pb_solver_status() == SolverStatus.unsat
    print("failed_assumps:", solver.failed_assumptions)
    assert len(solver.failed_assumptions) > 0

    solver.solve([ly1, ly3, ly4])
    assert solver.get_pb_solver_status() == SolverStatus.unsat
    print("failed_assumps:", solver.failed_assumptions)

    solver.solve([ly1, ly3])
    assert solver.get_pb_solver_status() == SolverStatus.sat
    assert len(solver.failed_assumptions) == 0
    model = solver.best_model
    print(", ".join(f"{solver.index2name(i)}={int(model[i])}" for i in range(len(model))))


def test_two_solvers():
    solver2 = naps.PbSolver()
    sat_case(solver2)

    solver1 = naps.PbSolver()
    solver1.report.setReportFlag(0, True)
    unsat_case(solver1)

    solver1.coding()
    solver1.solve()
    print(f"status: {solver1.get_pb_solver_status()}")
    assert solver1.get_pb_solver_status() == SolverStatus.unsat
    output(solver1)

    solver2.coding()
    solver2.solve()
    print(f"status: {solver1.get_pb_solver_status()}")
    assert solver2.get_pb_solver_status() == SolverStatus.sat
    output(solver2)


def test_minimize():
    solver1 = naps.PbSolverMinimize()

    sat_case(solver1)
    set_goal_case(solver1)

    solver1.coding()
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.best
    assert solver1.get_best_goalvalue() == -1


def test_hitting():
    config = Config()
    config.verbosity = 1
    config.minimization_strategy = MinT.Hitting
    solver1 = naps.PbSolverMinimize(config)

    sat_case(solver1)
    set_goal_case(solver1)

    solver1.coding()
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.best
    assert solver1.get_best_goalvalue() == -1


@needs_scip
def test_hitting_with_scip_inner():
    config = Config()
    config.verbosity = 1
    config.minimization_strategy = MinT.Hitting
    config.inner_with_scip = True
    solver1 = naps.PbSolverMinimize(config)

    sat_case(solver1)
    set_goal_case(solver1)

    solver1.coding()
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.best
    assert solver1.get_best_goalvalue() == -1


def test_min_multistage():
    solver1 = naps.PbSolverMinimize()

    min_multistage_case(solver1)

    solver1.coding()
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.best
    assert solver1.get_best_goalvalue() == 10005


def test_garden():
    config = Config()
    config.input = "Examples/garden9x9.opb"
    config.solver = SolverT.GlueMiniSat
    config.dis_conflict()

    pb_solver = naps.PbSolverMinimize(config)

    naps.parse_PB_file(config, config.input, pb_solver, config.old_format)

    pb_solver.coding()
    pb_solver.solve()
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == 20


@needs_scip
def test_scip_garden():
    config = Config()
    config.input = "Examples/garden9x9.opb"
    config.use_scip = True
    config.dis_conflict()

    pb_solver = naps.PbSolverMinimize(config)

    naps.parse_PB_file(config, config.input, pb_solver, config.old_format)

    pb_solver.coding()
    pb_solver.solve()
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == 20


def test_garden_with_extra_goal():
    config = Config()
    config.input = "Examples/garden9x9.opb"
    config.solver = SolverT.GlueMiniSat
    config.dis_conflict()
    config.repeated_optimizations = True

    pb_solver = naps.PbSolverMinimize(config)

    naps.parse_PB_file(config, config.input, pb_solver, config.old_format)

    _, assump_ps = pb_solver.coding([])   # code only once
    pb_solver.solve(assump_ps)
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == 20

    # add extra goal
    set_extraGoal_for_garden9x9(pb_solver)

    # assumptions をクリアしてから再 solve する必要がある
    pb_solver.solve([])
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == -2


@needs_scip
def test_scip_garden_with_extra_goal():
    config = Config()
    config.input = "Examples/garden9x9.opb"
    config.use_scip = True
    config.dis_conflict()
    config.repeated_optimizations = True

    pb_solver = naps.PbSolverMinimize(config)

    naps.parse_PB_file(config, config.input, pb_solver, config.old_format)

    _, assump_ps = pb_solver.coding([])   # code only once
    pb_solver.solve(assump_ps)
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == 20

    set_extraGoal_for_garden9x9(pb_solver)

    pb_solver.solve([])
    pb_solver.outputResult(pb_solver.get_pb_solver_status(), config, True)
    assert pb_solver.get_best_goalvalue() == -2


def test_counting():
    config = Config()
    config.command = Command.AllSolutions

    solver1 = naps.PbSolverCounting(config)

    sat_case(solver1)

    solver1.coding()
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.all
    assert solver1.get_number_models() == 2


def test_project_counting():
    config = Config()
    config.command = Command.InterestSolutions

    solver1 = naps.PbSolverCounting(config)

    sat_case(solver1)

    solver1.coding()
    solver1.addInterestVars(1)
    status = solver1.solve()
    print(f"status: {status}")

    output(solver1)
    assert solver1.get_pb_solver_status() == SolverStatus.all
    assert solver1.get_number_models() == 2
