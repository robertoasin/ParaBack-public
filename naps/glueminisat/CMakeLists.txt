cmake_minimum_required(VERSION 3.19)
project(glueminisat LANGUAGES CXX)

# MROOT相当の設定
set(MROOT ${CMAKE_CURRENT_SOURCE_DIR})

# 共通インクルードパス
include_directories(${MROOT})

# 共通ソース (utils, mtlなどはヘッダのみが多いが、.ccがあれば追加)
set(GLUE_UTILS_SRCS
    ${MROOT}/utils/System.cc
    ${MROOT}/utils/Stat.cc
    ${MROOT}/utils/Options.cc
)

# 各サブディレクトリへ
add_subdirectory(core)
add_subdirectory(simp)

