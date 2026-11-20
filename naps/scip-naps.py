#!/usr/bin/env python3
import contextlib
import io
import os
import shlex
import signal
import subprocess
import argparse
import sys
import tempfile
import math
from pyscipopt import Model

pb_proc = None
scip_naps_version = "1.00a2"

class SCIPLogger:
    """
    Custom logger to redirect SCIP output with 'c' prefixed comment style.
    """
    def __init__(self):
        self.partial_line = ""
        self.out_of_range_detected = False
        self.numerical_trouble_detected = False

    def write(self, msg):
        self.partial_line += msg
        lower_msg = msg.lower()
        if "out of range" in lower_msg:
            self.out_of_range_detected = True
        if "numerical troubles" in lower_msg:
            self.numerical_trouble_detected = True
        while '\n' in self.partial_line:
            line, self.partial_line = self.partial_line.split('\n', 1)
            sys.__stdout__.write(f"c {line}\n")

    def flush(self):
        if self.partial_line:
            sys.__stdout__.write(f"c {self.partial_line}\n")
            self.partial_line = ""

    def has_out_of_range_warning(self):
        return self.out_of_range_detected

    def has_numerical_troubles(self):
        return self.numerical_trouble_detected

def safe_int_bound(x, mode="floor", eps=1e-9):
    r = round(x)
    if abs(x - r) < eps:
        return r
    return math.floor(x) if mode == "floor" else math.ceil(x)

def print_solution(scip, sol, max_width):
    vars = scip.getVars()
    line = "v"
    lines = []
    for var in vars:
        val = int(round(scip.getSolVal(sol, var)))
        token = f" {var.name}" if val == 1 else f" -{var.name}"
        if len(line) + len(token) > max_width:
            lines.append(line)
            line = "v" + token
        else:
            line += token
    lines.append(line)
    for l in lines:
        print(l)

def append_fixed_constraints(scip, fout):
    num_fixed = 0
    for var in scip.getVars():
        lb = var.getLbGlobal()
        ub = var.getUbGlobal()
        if 0 < lb < 1 or 0 < ub < 1:
            print(f"c WARNING: variable {var.name} has fractional bounds: lb={lb}, ub={ub}")
        elif lb == ub:
            num_fixed += 1
            val = int(lb)
            fout.write(f"+1 {var.name} = {val} ;\n")
    return num_fixed

def handle_sigterm(signum, frame):
    global pb_proc
    if pb_proc:
        print("c received SIGTERM, forwarding to pbsolver...")
        sys.stdout.flush()
        try:
            os.killpg(os.getpgid(pb_proc.pid), signal.SIGTERM)
        except Exception as e:
            print(f"c failed to send SIGTERM to pbsolver: {e}")
    else:
        print(f"c received SIGTERM, exiting with code {128 + signum}")
        sys.stdout.flush()
        sys.exit(128 + signum)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

def run_pbsolver(cmd):
    try:
        global pb_proc
        pb_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid  # UNIX only: start new process group
        )

        stdout_lines = []
        for line in pb_proc.stdout:
            line = line.rstrip()
            stdout_lines.append(line)
            if not line.startswith("s ") and not line.startswith("v "):
                print(line)
            else:
                print(f"c {line}")
            sys.stdout.flush()
        pb_proc.wait()
        return "\n".join(stdout_lines)

    except KeyboardInterrupt:
        print("c received interrupt, forwarding to pbsolver...")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception as e:
            print(f"c failed to send SIGTERM to pbsolver: {e}")
        # try reading remaining output
        try:
            for line in proc.stdout:
                line = line.rstrip()
                stdout_lines.append(line)
                if not line.startswith("s ") and not line.startswith("v "):
                    print(line)
                else:
                    print(f"c {line}")
                sys.stdout.flush()
        except:
            pass
        proc.wait()
        return "\n".join(stdout_lines)

    except Exception as e:
        print(f"c error executing external solver: {e}")
        return "\n".join(stdout_lines)

def extract_results_from_output(output):
    for line in output.strip().splitlines():
        if line.startswith("v ") or line.startswith("s "):
            print(line)

def check_violated_constraints(scip, sol=None, tolerance=1e-9):
    if sol is None:
        sol = scip.getBestSol()
        if sol is None:
            print("No solution available.")
            return True

    no_violation = True

    for cons in scip.getConss(transformed=False):
        # Only check linear constraints
        if not cons.isLinear():
            continue

        lhs = scip.getLhs(cons)
        rhs = scip.getRhs(cons)
        activity = scip.getActivity(cons, sol)

        violation = 0.0
        if activity < lhs - tolerance:
            violation = lhs - activity
        elif activity > rhs + tolerance:
            violation = activity - rhs

        if violation > 0.0:
            no_violation = False
            print(f"[VIOLATION] {cons.name}: activity = {activity:.12g}, bounds = [{lhs}, {rhs}], violation = {violation:.3e}")
        # else:
        #     print(f"[OK] {cons.name}: activity = {activity:.12g}, bounds = [{lhs}, {rhs}], violation = {violation:.3e}")

    if no_violation:
        print("No constraint violations above tolerance.")

    return no_violation

def check_original_constraints_from_opb(opb_path, sol, scip):
    """
    Check if SCIP solution satisfies the original constraints in the .opb file.
    Returns True if all constraints are satisfied, False otherwise.
    Terminates on the first violation with a detailed report.
    """
    import re

    def eval_constraint(coeffs, op, rhs, assignment):
        val = sum(coeff * assignment.get(var, 0) for coeff, var in coeffs)
        return val, (
            val >= rhs if op == ">=" else
            val <= rhs if op == "<=" else
            val == rhs
        )

    # Build assignment from SCIP solution
    assignment = {}
    for var in scip.getVars():
        val = int(round(scip.getSolVal(sol, var)))
        assignment[var.name] = val

    with open(opb_path, "r") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("min:"):
                continue

            match = re.search(r"(<=|>=|=)", line)
            if not match:
                continue
            op = match.group(1)
            parts = line.split(op)
            if len(parts) != 2:
                continue

            lhs, rhs = parts
            rhs = rhs.rstrip(" ;").strip()
            try:
                rhs_val = int(rhs)
            except ValueError:
                continue

            # Parse coefficients and variables
            tokens = lhs.replace("+", " +").replace("-", " -").split()
            coeffs = []
            i = 0
            while i < len(tokens):
                try:
                    coeff = int(tokens[i])
                    var = tokens[i + 1]
                    coeffs.append((coeff, var))
                    i += 2
                except (IndexError, ValueError):
                    break

            activity, satisfied = eval_constraint(coeffs, op, rhs_val, assignment)
            if not satisfied:
                print(f"[VIOLATION] Line {lineno}: {line}")
                print(f"  Computed activity: {activity}")
                print(f"  Required: activity {op} {rhs_val}")
                print(f"  Variable assignments: {{", end="")
                print(", ".join(f"{v}={assignment.get(v, 0)}" for _, v in coeffs), end="}}\n")
                return False

    print("All original constraints are satisfied.")
    return True

def main():
    print("c SCIP-NaPS version: " + scip_naps_version)
    print("c Command: " + " ".join(sys.argv))

    parser = argparse.ArgumentParser(description="Run SCIP presolve and solve PB instance with optional external solver.")
    parser.add_argument("input", help="Input .opb file")
    parser.add_argument("--pbsolver", help="Path to external PB solver executable")
    parser.add_argument("--pbargs", type=str, help="Additional arguments (quoted) for PB solver, including instance")
    parser.add_argument("--dec", action="store_true", help="Solve as decision problem")
    parser.add_argument("--opt", action="store_true", help="Solve as optimization problem")
    parser.add_argument("--output", help="Output .opb file with fixed variables (if not specified, a temporary file will be used)")
    parser.add_argument("--no-write-fixed", dest="write_fixed", action="store_false",
                        help="Do not add fixed variables as constraints (default: add them)")
    parser.set_defaults(write_fixed=True)
    parser.add_argument("--sciptime", type=float, default=1000.0, help="Time limit for SCIP in seconds")
    parser.add_argument("--linewidth", type=int, default=2000, help="Line width for 'v' lines (default: 80)")
    args = parser.parse_args()

    input_opb = args.input
    output_opb = args.output or tempfile.NamedTemporaryFile(delete=False, suffix=".opb").name
    pb_solver_path = os.path.expanduser(args.pbsolver) if args.pbsolver else None
    max_width = args.linewidth

    if args.dec and args.opt:
        print("c ERROR: --dec and --opt cannot be specified at the same time.")
        sys.exit(1)
    if not args.dec and not args.opt:
        with open(input_opb, "r") as f:
            for line in f:
                if line.strip().startswith("min:"):
                    args.opt = True
                    break
            else:
                args.dec = True
    print(f"c Solving mode: {'optimization' if args.opt else 'decision'}")

    log = SCIPLogger()
    sys.stdout = log
    sys.stderr = log

    scip = Model()
    scip.redirectOutput()
    scip.setRealParam("limits/time", args.sciptime)
    # scip.setRealParam("numerics/feastol", 1e-9)
    # scip.setParam("numerics/epsilon", 1e-12)
    # scip.setParam("numerics/sumepsilon", 1e-10)
    scip.printVersion()

    scip_failed = False
    try:
        scip.readProblem(input_opb)
        scip.presolve()
    except Exception as e:
        sys.stdout = sys.__stdout__
        print("c SCIP failed to read or presolve the input problem.")
        print(f"c Reason: {e}")
        scip_failed = True

    if log.has_out_of_range_warning():
        sys.stdout = sys.__stdout__
        print("c WARNING: SCIP detected coefficients or weights that may be out of range.")

    if not scip_failed and not log.has_out_of_range_warning():
        scip.optimize()

        status = scip.getStatus()
        dual = scip.getDualbound()
        primal = scip.getPrimalbound()

        if status == 'infeasible':
            sys.stdout = sys.__stdout__
            print("c SCIP found the problem to be infeasible.")
            print("s UNSATISFIABLE")
            sys.exit(0)

        # Check if SCIP found a feasible solution
        has_valid_result = True
        if scip.getNSols() > 0:
            sol = scip.getBestSol()

            if scip.checkSol(sol, original=True) and check_original_constraints_from_opb(input_opb, sol, scip): # Double-check feasibility against original constraints
                print("SCIP's best solution is verified feasible for the original problem.")
            else:
                has_valid_result = False
                print("SCIP found a solution, but it is not feasible according to the original problem.")

        if log.has_numerical_troubles():
            has_valid_result = False
            print("WARNING: SCIP encountered numerical troubles during optimization.")

        sys.stdout = sys.__stdout__

        if status == 'optimal' and has_valid_result:

            sol = scip.getBestSol()
            if args.dec:
                print("c SCIP found a feasible solution.")
                print("s SATISFIABLE")
            else:
                print("c SCIP found an optimal solution.")
                print(f"o {round(scip.getObjVal())}")
                print("s OPTIMUM FOUND")
            print_solution(scip, sol, max_width)
            sys.exit(0)

        if has_valid_result:
            print("c ----------------------------------------------------------------------")
            goal_lb = safe_int_bound(dual, mode="ceil") if math.isfinite(dual) else None
            print(f"c dual bound: {dual} -> {goal_lb}" if goal_lb is not None else "c dual bound is infinite")

            goal_ub = safe_int_bound(primal, mode="floor") if math.isfinite(primal) else None
            print(f"c primal bound: {primal} -> {goal_ub}" if goal_ub is not None else "c primal bound is infinite")

            print(f"c scip-goal-lb: {goal_lb}")
            print(f"c scip-goal-ub: {goal_ub}")

            print(f"c writing output to: {output_opb}")
            with open(input_opb, "r") as fin, open(output_opb, "w") as fout:
                for line in fin:
                    fout.write(line)

                if args.write_fixed:
                    print("c appending fixed variable constraints")
                    num_fixed = append_fixed_constraints(scip, fout)
                    num_vars = scip.getNVars()
                    print(f"c scip-fixed vars: {100 * num_fixed / num_vars:.2f}% ({num_fixed}/{num_vars})")

            if not pb_solver_path:
                if scip.getNSols() > 0:
                    sol = scip.getBestSol()
                    if args.opt:
                        print(f"o {round(scip.getObjVal())}")
                    print_solution(scip, sol, max_width)
                    print("s SATISFIABLE")
                else:
                    print("s UNKNOWN")
                sys.exit(0)

            cmd = [pb_solver_path]
            if args.opt:
                if goal_lb is not None:
                    cmd.append(f"-goal-lb={goal_lb}")
                if goal_ub is not None:
                    cmd.append(f"-goal-ub={goal_ub - 1}")
            cmd.append(output_opb)

            if args.pbargs:
                try:
                    cmd.extend(shlex.split(args.pbargs))
                except ValueError as e:
                    print(f"c error parsing --pbargs: {e}")
                    sys.exit(1)

            print("c ----------------------------------------------------------------------")
            print(f"c executing external solver: {pb_solver_path}")
            print("c ----------------------------------------------------------------------")
            print(f"c command: {' '.join(cmd)}")
            sys.stdout.flush()

            stdout = run_pbsolver(cmd)

            if scip.getNSols() > 0:
                if "s UNSATISFIABLE" in stdout:
                    sol = scip.getBestSol()
                    print_solution(scip, sol, max_width)
                    print(f"o {goal_ub}")
                    print("s OPTIMUM FOUND")
                elif "s SATISFIABLE" in stdout or "s OPTIMUM FOUND" in stdout:
                    extract_results_from_output(stdout)
                else:
                    sol = scip.getBestSol()
                    print_solution(scip, sol, max_width)
                    print(f"o {goal_ub}")
                    print("s SATISFIABLE")
            else:
                extract_results_from_output(stdout)
            sys.exit(0)

    if pb_solver_path:
        cmd = [pb_solver_path, input_opb]
        if args.pbargs:
            try:
                cmd.extend(shlex.split(args.pbargs))
            except ValueError as e:
                print(f"c error parsing --pbargs: {e}")
                sys.exit(1)

        print("c ----------------------------------------------------------------------")
        print(f"c executing external solver (fallback): {pb_solver_path}")
        print("c ----------------------------------------------------------------------")
        print(f"c command: {' '.join(cmd)}")
        sys.stdout.flush()

        stdout = run_pbsolver(cmd)
        extract_results_from_output(stdout)
    else:
        print("c No external PB solver specified; nothing to do.")
    sys.exit(0)

if __name__ == "__main__":
    main()
