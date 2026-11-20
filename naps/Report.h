#ifndef Report_h
#define Report_h

#include <cstdarg>
#include <string>
#include "Global.h"

//- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
class Report {
public:
    int reportLevel;
    std::string reportPrefix;
    bool ansi;
    bool reportSingleMode;
    char reportSingleChar;
    int  reportSingleCount;
    bool col0;
    bool bold;

    Report();

    void setReportFlag(int v, bool a);
    void deepenPrefix() { reportPrefix = "c" + reportPrefix; }

    void reportf(const char* format, ...);                 // 'printf()' replacer -- will put "c " first at each line if 'opt_satlive' is TRUE.
    void reportf(int level, const char* format, ...);      // 'printf()' replacer -- will put "c " first at each line if 'opt_satlive' is TRUE.
    void reportSf(int level, const char c);                // Single charactor mode of reportf, which reports the number of repetition.

private:
    void reportfvns(const char* format, va_list args);
};

// extern Report report;

// #define setReportFlag ::report.setReportFlag
// #define reportf ::report.reportf
// #define reportSf ::report.reportSf

#endif
