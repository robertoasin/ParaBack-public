# NaPS

See `README_SCIP-NaPS.md` for `SCIP-NaPS`

## Overall:

*NaPS (Nagoya pseudo-Boolean solver)* is a pseudo-Boolean solver for linear problems with Boolean variables developed based on *MiniSAT+* at Nagoya University

Linear constraint ```y1 - 3*y2 = 0``` is described as
```1 y1 -3 y2 = 0 ;```

NaPS follows MiniSAT+ for the input format, for example:

```
* #variable= 2 #constraint= 2
min: 1 x1 1 y1 ;
1 x1 -1 y1 >= 0 ;
1 x1  2 y1 <= 2 ;
```

where the first two lines are optional.

---

## Descriptions

### [Negated Boolean variable]

```~x1``` denotes the negation of ```x1``` as the following example:
```1 ~x1 2 x2 3 ~x3 = 3;```

### [facility of definition]

In addition to ordinary constraints interpreted conjunction.  This solver has a facility of definition, which enables us to write complex formulas like CNFs.  For example,
```d x3 => 1 x1 -2 x2 > 0 ;```
represents a constraint meaning
```x3 implies x1 - 2*x2 >0```

As another example, the following description denotes (```c1``` or ```c2```) and ((not ```c2```) or (not ```c3```)):

```
d x1 => c1 ;
d x2 <=> c2 ;
d x3 <= c3 ;
1 x1 1 x2 >= 1 ;
1 ~x2 1 ~x3 >= 1 ;
```

where in real description, ```c1```, ```c2```, and ```c3``` should be replaced by linear constraints.

### [minimization]

Minimizing goal value facility is inherited from minisatp. 
e.g. 
```min: 1 x1 -1 x2```

### [model counting]

```-A``` option will find the number of models, inherited from MinisatP.  Projection model counting is also possible by ```-AI```, where variables that appear in the minimization goal are focused on.  In this case, the minimization goal is ignored and used only for denoting a set of variables.

### [soft constraint]

Soft constraints are accepted, like 
```[3] 1 y1 3 = 0```
```[3]``` shows the penalty in case of the model does not satisfy this.  The solver tries to find a model with minimum sum of penalties.

## Optional input formats

### [dimacs inputs]

```-dimacs``` option enables to read dimacs cnf files.

### [maxsat mode]

```-max-sat``` option states to solve maxsat cnf/wcnf.

## Conversion into CNF

'-cnf=filename.cnf' option will produce a DMACS-CNF file from PB constraints.  

* The correspondence between variables in 'opb' input and those in 'cnf' output is added in around the last line as a comment heading with 'cv', like:
  ```cv 1:x1 2:x2 3:x3 ... ```

* Variables that appear in the minimization goal are also added in the last line as a comment.  For example,  the line
  ```c p show 1 2 0```
  indicates that those CNF variables are 1 and 2.

---

## Usage and Installation

For showing its usage, try 
  ```naps --help```

See `INSTALL.md` for its installation.

---

## Known BUGs

* ```-S``` may result wrong answer.
* Combination of ```-Simp``` and ```-es1``` may result wrong answer.

---

## History

NaPS 1.00 is a release, renamed version of gpw-2.18, where gpw solvers are developed based on minisatp-1.0: 
  http://minisat.se/MiniSat+.html 
We thank Niklas Een and Niklas Srensson, who developed nice platform MiniSat+.

NaPS distribution contains several SAT-solvers.  Recent NaPS invokes Kissat for Decision problems and CaDiCaL for Optimization problems by default.

All versions of gpw-2.x and NaPS-1.x are developed by Masahiko Sakai and his colleges.

### Version Renaming

`1.04a9` into `1.4.1.dev1`

---
