#include "Report.h"

#undef setReportFlag
#undef reportf
#undef reportSf

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Report report;

Report::Report() : reportLevel(0), reportPrefix("c "), ansi(false), reportSingleMode(false), reportSingleCount(0), col0(true), bold(false) {}

void Report::setReportFlag(int v, bool a) {
  reportLevel = v;
}

void Report::reportSf(int level, char c)
{
    if (level > reportLevel) return;

  if(!reportSingleMode) {
    reportSingleCount = 1;
    reportSingleMode = true;
    reportSingleChar = c;
  } else {
    if ( c == reportSingleChar) reportSingleCount++;
    else {
      reportf(level, "%c: %d\n", reportSingleChar, reportSingleCount);
      reportSingleCount = 1;
      reportSingleMode = true;
      reportSingleChar = c;
    }
  }

}

void Report::reportfvns(const char* format, va_list args)
{
  if(reportSingleMode) {
    reportSingleMode = false;
    reportf("%c: %d\n", reportSingleChar, reportSingleCount);
    reportSingleCount = 0;
  }

  char* text = vnsprintf(format, args);
  va_end(args);

  for(char* p = text; *p != 0; p++){
    if (col0)  // && opt_satlive is omitted
      for (char c : reportPrefix) putchar(c);

    if (*p == '\b'){
      bold = !bold;
      if (ansi)
        putchar(27), putchar('['), putchar(bold?'1':'0'), putchar('m');
      col0 = false;
    }else{
      putchar(*p);
      col0 = (*p == '\n' || *p == '\r');
    }
  }
  fflush(stdout);
  xfree(text);
}

void Report::reportf(int level, const char* format, ...)
{
  if (level > reportLevel) return;

  va_list args;
  va_start(args, format);
  reportfvns(format, args);
  va_end(args);
}


void Report::reportf(const char* format, ...)
{
  if (1 > reportLevel) return;

  va_list args;
  va_start(args, format);
  reportfvns(format, args);
  va_end(args);
}
