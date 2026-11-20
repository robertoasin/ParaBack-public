//================================================================================[naps_bindings.cpp]
// pybind11 による naps の Python バインディング。
//
// 方針: C++ API（tests/pb_solver_test.cpp が使う範囲）を同名でミラーする薄いバインディング。
//   - 係数・右辺値は Python の任意精度 int を 10 進文字列経由で GMP ベースの Int に変換する
//   - PbSolver は渡された Config / Solver を所有しないため、py::keep_alive で寿命を束ねる
//   - solve / coding / convertPbs / parse_PB_file の実行中は GIL を解放する
//
// 注意（C++ 側の意味論の反映）:
//   - PbSolver_Minimize / PbSolver_Counting は solve(vec<Lit>&) をオーバーライドするが
//     solve(vec<Lit>&, vec<Lit>&) はオーバーライドしない。そのため assumption 付き solve は
//     基底クラスでは 2 引数版（failed_assumptions が得られる）、派生クラスでは 1 引数版
//     （最適化・数え上げが行われる）を呼び分ける。Python の名前解決はクラス単位なので
//     各派生クラスで solve を再定義している。

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>
#include <vector>
#include <optional>

#include "Config.h"
#include "PbSolver.h"
#include "PbParser.h"
#include "Report.h"
#include "Version.h"

namespace py = pybind11;

//=================================================================================================
// Int（GMP 任意精度）と Python int の相互変換

static Int pyToInt(const py::object& o) {
    if (!py::isinstance<py::int_>(o))
        throw py::type_error("expected int");
    std::string s = py::str(o);
    return Int(s.c_str());
}

// Int_MIN / Int_MAX は「未定義」を表す番兵なので None にマップする。
static py::object intToPy(const Int& v) {
    if (v == Int_MIN || v == Int_MAX)
        return py::none();
    char* s = toString(v);
    py::object r = py::reinterpret_steal<py::object>(PyLong_FromString(s, nullptr, 10));
    xfree(s);
    if (!r) throw py::error_already_set();
    return r;
}

//=================================================================================================
// vec<T> と std::vector<T> の相互変換

static void toVecLit(const std::vector<Lit>& in, vec<Lit>& out) {
    out.clear();
    for (Lit p : in) out.push(p);
}

static std::vector<Lit> fromVecLit(const vec<Lit>& in) {
    std::vector<Lit> r;
    for (int i = 0; i < in.size(); i++) r.push_back(in[i]);
    return r;
}

static void toVecInt(const py::sequence& in, vec<Int>& out) {
    out.clear();
    for (auto o : in) out.push(pyToInt(py::reinterpret_borrow<py::object>(o)));
}

//=================================================================================================
// solve / coding の共通ラッパー

// 基底 PbSolver 用: 2 引数版 solve を呼び、failed_assumptions を動的属性へ格納する。
static solverStatus solveBaseWithAssumps(py::object self, const std::vector<Lit>& assumps) {
    PbSolver& s = self.cast<PbSolver&>();
    vec<Lit> aps; toVecLit(assumps, aps);
    vec<Lit> failed;
    solverStatus st;
    {
        py::gil_scoped_release release;
        st = s.solve(aps, failed);
    }
    self.attr("failed_assumptions") = py::cast(fromVecLit(failed));
    return st;
}

// 派生クラス用: 1 引数版 solve（仮想）を呼ぶ。failed_assumptions は得られない。
static solverStatus solveDerivedWithAssumps(py::object self, const std::vector<Lit>& assumps) {
    PbSolver& s = self.cast<PbSolver&>();
    vec<Lit> aps; toVecLit(assumps, aps);
    solverStatus st;
    {
        py::gil_scoped_release release;
        st = s.solve(aps);
    }
    self.attr("failed_assumptions") = py::list();
    return st;
}

static solverStatus solveNoAssumps(py::object self) {
    PbSolver& s = self.cast<PbSolver&>();
    solverStatus st;
    {
        py::gil_scoped_release release;
        st = s.solve();
    }
    self.attr("failed_assumptions") = py::list();
    return st;
}

//=================================================================================================

PYBIND11_MODULE(naps, m) {
    m.doc() = "Python bindings for the naps Pseudo-Boolean solver (thin C++ mirror API)";
    m.attr("__version__") = naps_version;
#ifdef USE_SCIP
    m.attr("HAS_SCIP") = true;
#else
    m.attr("HAS_SCIP") = false;
#endif

    //--- enum ------------------------------------------------------------------------------------
    py::enum_<solverStatus>(m, "SolverStatus")
        .value("unknown", sst_unknown)
        .value("sat",     sst_sat)
        .value("unsat",   sst_unsat)
        .value("best",    sst_best)
        .value("all",     sst_all)
        .value("cnf",     sst_cnf);

    py::enum_<SolverT>(m, "SolverT")
        .value("Default",     st_Default)
        .value("MiniSat",     st_MiniSat)
        .value("SatELite",    st_SatELite)
        .value("GlueMiniSat", st_GlueMiniSat)
        .value("MiniSat22",   st_MiniSat22)
        .value("Glucose10",   st_Glucose10)
        .value("ES1Sat",      st_ES1Sat)
        .value("CCMiniSat",   st_CCMiniSat)
        .value("CaDiCaL",     st_CaDiCaL)
        .value("Kissat",      st_Kissat);

    py::enum_<Command>(m, "Command")
        .value("Minimize",          cmd_Minimize)
        .value("FirstSolution",     cmd_FirstSolution)
        .value("AllSolutions",      cmd_AllSolutions)
        .value("InterestSolutions", cmd_InterestSolutions)
        .value("Soft",              cmd_Soft);

    py::enum_<MinT>(m, "MinT")
        .value("Sequential",         mt_Sequential)
        .value("Binary",             mt_Binary)
        .value("Hitting",            mt_Hitting)
        .value("Sequential_Binary",  mt_Sequential_Binary)
        .value("Sequential_Hitting", mt_Sequential_Hitting)
        .value("Sc2",                mt_Sc2)
        .value("Sequential_Sc2",     mt_Sequential_Sc2);

    //--- Lit -------------------------------------------------------------------------------------
    py::class_<Lit>(m, "Lit")
        .def(py::init([](int v, bool s) {
                 if (v < 0) throw py::value_error("var must be >= 0");
                 return Lit(v, s);
             }),
             py::arg("var"), py::arg("sign") = false)
        .def_property_readonly("var",  [](Lit p) { return var(p); })
        .def_property_readonly("sign", [](Lit p) { return sign(p); })
        .def("__invert__", [](Lit p) { return ~p; })
        .def("__eq__", [](Lit p, py::object q) {
                 return py::isinstance<Lit>(q) && p == q.cast<Lit>();
             }, py::is_operator())
        .def("__hash__", [](Lit p) { return index(p); })
        .def("__repr__", [](Lit p) {
                 return std::string(sign(p) ? "~x" : "x") + std::to_string(var(p));
             });

    //--- Report ----------------------------------------------------------------------------------
    py::class_<Report>(m, "Report")
        .def(py::init<>())
        .def("setReportFlag", &Report::setReportFlag, py::arg("v"), py::arg("a"))
        .def("reportf", [](Report& r, const std::string& msg) { r.reportf("%s", msg.c_str()); },
             py::arg("message"));

    //--- Config ----------------------------------------------------------------------------------
    py::class_<Config>(m, "Config")
        .def(py::init<>())
        .def_readwrite("verbosity",              &Config::verbosity)
        .def_readwrite("use_scip",               &Config::use_scip)
        .def_readwrite("inner_with_scip",        &Config::inner_with_scip)
        .def_readwrite("repeated_optimizations", &Config::repeated_optimizations)
        .def_readwrite("minimization_strategy",  &Config::minimization_strategy)
        .def_readwrite("command",                &Config::command)
        .def_readwrite("solver",                 &Config::solver)
        .def_readwrite("old_format",             &Config::old_format)
        .def_property("input",
             [](const Config& c) -> py::object {
                 return c.input ? py::object(py::str(c.input)) : py::none();
             },
             [](Config& c, const std::string& v) { c.input = xstrdup(v.c_str()); })
        .def_property("report",
             [](Config& c) -> Report& { return c.report; },
             [](Config& c, const Report& r) { c.report = r; },
             py::return_value_policy::reference_internal)
        .def("dis_conflict", &Config::dis_conflict);

    //--- Solver（opaque; 外部注入用） --------------------------------------------------------------
    py::class_<Solver>(m, "Solver");

    m.def("Solver_invoke",
          [](SolverT stype, Config* config) { return Solver_invoke(stype, config); },
          py::arg("solver_type"), py::arg("config"),
          py::return_value_policy::take_ownership, py::keep_alive<0, 2>());

    //--- PbSolver --------------------------------------------------------------------------------
    py::class_<PbSolver>(m, "PbSolver", py::dynamic_attr())
        .def(py::init<>())
        .def(py::init<Config*>(), py::arg("config"), py::keep_alive<1, 2>())
        .def(py::init<Config*, Solver*>(), py::arg("config"), py::arg("sat_solver"),
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def("getVar",
             [](PbSolver& s, const std::string& name) { return s.getVar(name.c_str()); },
             py::arg("name"))
        .def("addConstr",
             [](PbSolver& s, const std::vector<Lit>& ps_in, const py::sequence& Cs_in,
                py::object rhs, int ineq, py::object rhs2, int ineq2,
                std::optional<Lit> llt, std::optional<Lit> rlt, bool normalized) {
                 vec<Lit> ps; toVecLit(ps_in, ps);
                 vec<Int> Cs; toVecInt(Cs_in, Cs);
                 Int r  = pyToInt(rhs);
                 Int r2 = rhs2.is_none() ? Int_MAX : pyToInt(rhs2);
                 return s.addConstr(normalized, ps, Cs, r, ineq, r2, ineq2,
                                    llt.value_or(lit_Undef), rlt.value_or(lit_Undef));
             },
             py::arg("ps"), py::arg("Cs"), py::arg("rhs"), py::arg("ineq"),
             py::arg("rhs2") = py::none(), py::arg("ineq2") = -1,
             py::arg("llt") = py::none(), py::arg("rlt") = py::none(),
             py::arg("normalized") = false)
        .def("addGoal",
             [](PbSolver& s, const std::vector<Lit>& ps_in, const py::sequence& Cs_in) {
                 vec<Lit> ps; toVecLit(ps_in, ps);
                 vec<Int> Cs; toVecInt(Cs_in, Cs);
                 s.addGoal(ps, Cs);
             },
             py::arg("ps"), py::arg("Cs"))
        .def("coding",
             [](PbSolver& s) {
                 py::gil_scoped_release release;
                 return s.coding();
             })
        .def("coding",
             [](PbSolver& s, const std::vector<Lit>& assumps) {
                 vec<Lit> aps; toVecLit(assumps, aps);
                 solverStatus st;
                 {
                     py::gil_scoped_release release;
                     st = s.coding(aps);
                 }
                 return py::make_tuple(st, fromVecLit(aps));
             },
             py::arg("assumps"))
        .def("solve", &solveNoAssumps)
        .def("solve", &solveBaseWithAssumps, py::arg("assumps"))
        .def("convertPbs",
             [](PbSolver& s, int stage) {
                 py::gil_scoped_release release;
                 return s.convertPbs(stage);
             },
             py::arg("stage"))
        .def("addInterestVars", &PbSolver::addInterestVars, py::arg("num"))
        .def("okay", &PbSolver::okay)
        .def("nVars", &PbSolver::nVars)
        .def("nConstrs", &PbSolver::nConstrs)
        .def("get_pb_solver_status", &PbSolver::get_pb_solver_status)
        .def("set_pb_solver_status", &PbSolver::set_pb_solver_status, py::arg("status"))
        .def("get_best_goalvalue",
             [](PbSolver& s) { return intToPy(s.get_best_goalvalue()); })
        .def("get_number_models", &PbSolver::get_number_models)
        .def("index2name",
             [](PbSolver& s, int i) {
                 if (i < 0 || i >= s.named_vars.size())
                     throw py::index_error("no such variable index");
                 return std::string(s.index2name[s.named_vars[i]]);
             },
             py::arg("i"),
             "Name of the i-th named variable in getVar() registration order.\n"
             "'i' is the position in 'best_model' (NOT the SAT var index returned by\n"
             "getVar(); the two coincide only for vars registered before the first\n"
             "coding()/clausification).")
        .def_property_readonly("best_model",
             [](PbSolver& s) {
                 // Dense list over named vars in getVar() registration order, so that
                 // best_model[i] and index2name(i) always refer to the same variable.
                 // Unnamed (clausification/solver-internal) vars are not exposed.
                 std::vector<bool> r;
                 for (int i = 0; i < s.named_vars.size(); i++) {
                     Var v = s.named_vars[i];
                     if (v >= s.best_model.size()) break;  // vars registered after the last commit
                     r.push_back(s.best_model[v]);
                 }
                 return r;
             })
        .def_property_readonly("report",
             [](PbSolver& s) { return s.report; },
             py::return_value_policy::reference_internal)
        .def_property_readonly("config",
             [](PbSolver& s) { return s.config; },
             py::return_value_policy::reference_internal)
        .def("outputResult",
             [](PbSolver& s, solverStatus status, Config* config, bool normalExit) {
                 s.outputResult(status, config, normalExit);
             },
             py::arg("status"), py::arg("config"), py::arg("normalExit") = true);

    //--- PbSolverMinimize ------------------------------------------------------------------------
    py::class_<PbSolver_Minimize, PbSolver>(m, "PbSolverMinimize", py::dynamic_attr())
        .def(py::init<>())
        .def(py::init<Config*>(), py::arg("config"), py::keep_alive<1, 2>())
        .def(py::init<Config*, Solver*>(), py::arg("config"), py::arg("sat_solver"),
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def("solve", &solveNoAssumps)
        .def("solve", &solveDerivedWithAssumps, py::arg("assumps"));

    //--- PbSolverCounting ------------------------------------------------------------------------
    py::class_<PbSolver_Counting, PbSolver>(m, "PbSolverCounting", py::dynamic_attr())
        .def(py::init<>())
        .def(py::init<Config*>(), py::arg("config"), py::keep_alive<1, 2>())
        .def(py::init<Config*, Solver*>(), py::arg("config"), py::arg("sat_solver"),
             py::keep_alive<1, 2>(), py::keep_alive<1, 3>())
        .def("solve", &solveNoAssumps)
        .def("solve", &solveDerivedWithAssumps, py::arg("assumps"));

    //--- モジュール関数 -----------------------------------------------------------------------------
    m.def("parse_PB_file",
          [](Config* config, const std::string& filename, PbSolver& solver,
             bool old_format, bool abort_on_error) {
              py::gil_scoped_release release;
              parse_PB_file(config, filename.c_str(), solver, old_format, abort_on_error);
          },
          py::arg("config"), py::arg("filename"), py::arg("solver"),
          py::arg("old_format") = false, py::arg("abort_on_error") = true);
}
