# simpのソース
file(GLOB SIMP_SRCS "SimpSolver.cc") # Main.cc以外

# 1. ライブラリの作成 (libglueminisat.a)
add_library(libglueminisat STATIC ${SIMP_SRCS} $<TARGET_OBJECTS:glueminisat_core>)
# target_link_libraries(libglueminisat PUBLIC glueminisat_core)

# 出力ファイル名を "libglueminisat.a" に固定（ターゲット名と別に設定）
set_target_properties(libglueminisat PROPERTIES OUTPUT_NAME "glueminisat")

# 2. 実行形式の作成
# ターゲット名を "glueminisat_exe" などに変える
add_executable(glueminisat_app Main.cc)
target_link_libraries(glueminisat_app PRIVATE libglueminisat z)

# 出力されるバイナリ名を "glueminisat" に設定
set_target_properties(glueminisat_app PROPERTIES OUTPUT_NAME "glueminisat")

