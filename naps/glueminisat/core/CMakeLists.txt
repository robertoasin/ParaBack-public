# coreのソース
file(GLOB CORE_SRCS "Solver.cc") # Main.cc以外

# ライブラリ (libglueminisat_core.a) の作成
add_library(glueminisat_core OBJECT ${CORE_SRCS} ${GLUE_UTILS_SRCS})

# 単体実行形式 (glueminisat-core)
add_executable(glueminisat-core Main.cc)
target_link_libraries(glueminisat-core PRIVATE glueminisat_core z)

