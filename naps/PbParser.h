/**************************************************************************************[PbParser.h]
  Copyright (c) 2005-2010, Niklas Een, Niklas Sorensson

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction,
  including without limitation the rights to use, copy, modify, merge, publish, distribute,
  sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all copies or
  substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
  NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
  DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
  OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 **************************************************************************************************/

#ifndef PbParser_h
#define PbParser_h

#include "PbSolver.h"


//=================================================================================================

// class PbParser {
//   Config* config;
//   PbParser(Config* config) : config(config) {};

//   void parseInterestVars(B& in, S& solver);
//   void parseGoal(B& in, S& solver, bool old_format);
//   int parseInequality(B& in);
//   bool parseConstrs(B& in, S& solver, bool old_format);
//   bool parse_PB(B& in, S& solver, bool old_format, bool abort_on_error);
//   bool parse_Dimacs(B& in, S& solver, bool max_sat_mode, bool abort_on_error);
// }

void parse_PB_file(Config* config, cchar* filename, PbSolver& solver, bool old_format = (false), bool abort_on_error = (true));
void parse_Dimacs_file(Config* config, cchar* filename, PbSolver& solver, bool max_sat_mode = (false), bool abort_on_error = (true));
bool prefetchGoal_file(cchar* filename);
void parse_Map_file(Config* config,cchar* filename, PbSolver& solver, bool abort_on_error = (true));
//=================================================================================================

#endif
