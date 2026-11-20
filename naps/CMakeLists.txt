cmake_minimum_required(VERSION 3.19)

include(ExternalProject)

# --- 1. コンパイラ・基本フラグ設定 ---
option(USE_SYSTEM_COMPILER "Use system default compiler instead of prioritizing g++" OFF)

# project() を呼ぶ前に設定する必要がある
if(NOT CMAKE_CXX_COMPILER AND NOT USE_SYSTEM_COMPILER)
    if(CMAKE_HOST_SYSTEM_NAME STREQUAL "FreeBSD")
        file(GLOB GXX_LIST "/usr/local/bin/g++[0-9]*")
        if(GXX_LIST)
            list(SORT GXX_LIST COMPARE NATURAL ORDER DESCENDING)
            list(GET GXX_LIST 0 LATEST_GXX)
            set(CMAKE_CXX_COMPILER ${LATEST_GXX})

            # gccの設定
            string(REPLACE "g++" "gcc" LATEST_GCC ${LATEST_GXX})
            set(CMAKE_C_COMPILER ${LATEST_GCC})
        endif()

    elseif(APPLE)
        file(GLOB GXX_LIST "/opt/homebrew/bin/g++-[0-9]*" "/usr/local/bin/g++-[0-9]*")
        if(GXX_LIST)
            list(SORT GXX_LIST ORDER DESCENDING)
            list(GET GXX_LIST 0 LATEST_GXX)
            set(CMAKE_CXX_COMPILER ${LATEST_GXX})
        endif()

    else()
        # Linux 等で c++ よりも g++ を優先する場合
        find_program(GPP_EXECUTABLE g++)
        if(GPP_EXECUTABLE)
            set(CMAKE_CXX_COMPILER ${GPP_EXECUTABLE})
        endif()
    endif()
endif()

# バージョン付き g++ (g++12 など) の正規化処理
if(CMAKE_CXX_COMPILER MATCHES "g\\+\\+([0-9]+)$")
    set(GPP_VER ${CMAKE_MATCH_1})
    # ここで上書きしなくても、フルパスがセットされていればOK
endif()

# ここで CXX を定義
project(naps LANGUAGES C CXX)

message(STATUS "Using CXX compiler: ${CMAKE_CXX_COMPILER}")


if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD" AND CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    get_filename_component(GPP_BIN_DIR ${CMAKE_CXX_COMPILER} DIRECTORY)
    get_filename_component(GPP_BASE_DIR ${GPP_BIN_DIR} DIRECTORY)

    set(GPP_LIB_PATH "${GPP_BASE_DIR}/lib/gcc${GPP_VER}")

#    set(CMAKE_SKIP_BUILD_RPATH FALSE)
#    set(CMAKE_INSTALL_RPATH "${GPP_LIB_PATH}")
#    set(CMAKE_BUILD_WITH_INSTALL_RPATH TRUE)
    set(GPP_LD_FLAGS "-Wl,-rpath,${GPP_LIB_PATH}")
    add_link_options(${GPP_LD_FLAGS})
#    set(EXT_CONFIG_OPT CXXFLAGS=${GPP_LD_FLAGS})

endif()

# --- 2. オプション設定 ---
option(USE_GMP "Use GMP library" ON)
option(STATIC_BUILD "Build static executable" OFF)
option(USE_64BIT_INT "Use 64-bit integers instead of GMP" OFF)
option(USE_SCIP "Use SCIP library" ON)
option(USE_CADICAL2 "Use CaDiCaL 2.x (cadical2/) instead of 3.x (cadical3/)" OFF)
option(BUILD_PYTHON "Build Python bindings (pybind11)" OFF)

if(USE_CADICAL2)
    set(CADICAL_SRC_DIR "cadical2")
    message(STATUS "CaDiCaL: using version 2.x (cadical2/)")
else()
    set(CADICAL_SRC_DIR "cadical3")
    message(STATUS "CaDiCaL: using version 3.x (cadical3/)")
endif()

# Makefile の CFLAGS を反映
# add_compile_options(-Wall -ffloat-store -fno-strict-aliasing -fpermissive)
add_compile_options(-Wall -ffloat-store -fno-strict-aliasing)
add_definitions(-D_FILE_OFFSET_BITS=64 -D__STDC_LIMIT_MACROS -D__STDC_FORMAT_MACROS)

# インクルードパス
include_directories(
    .
    ADTs
    glueminisat
    /usr/local/include
    /opt/homebrew/include
)

set(EXT_CONFIG_OPT "")
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(EXT_CONFIG_OPT ${EXT_CONFIG_OPT} -g)
    set(EXT_BUILD_SUBDIR "build_debug")
else()
    set(EXT_BUILD_SUBDIR "build")
endif()

if(BUILD_PYTHON)
    # Python 拡張モジュール（共有ライブラリ）に静的リンクするため全オブジェクトを PIC でビルドする。
    # 非 PIC の通常ビルド成果物と混ざらないよう外部ライブラリのビルドディレクトリも分離する。
    set(CMAKE_POSITION_INDEPENDENT_CODE ON)
    set(EXT_CONFIG_OPT ${EXT_CONFIG_OPT} -fPIC)
    set(EXT_BUILD_SUBDIR "${EXT_BUILD_SUBDIR}_pic")
endif()

# ${CADICAL_SRC_DIR}/build/libcadical.a を生成するように指示
ExternalProject_Add(ext_cadical
    SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/${CADICAL_SRC_DIR}"
    # STAMP_DIR は BINARY_DIR 配下に置く。ビルド構成ごとに BINARY_DIR が
    # 分かれる(build / build_debug / build_pic)ため、stamp を共有すると
    # 先にビルドした構成の stamp で他構成がスキップされ .a が生成されない。
    STAMP_DIR  "${CMAKE_CURRENT_SOURCE_DIR}/${CADICAL_SRC_DIR}/${EXT_BUILD_SUBDIR}/cmake_stamps"
    BINARY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/${CADICAL_SRC_DIR}/${EXT_BUILD_SUBDIR}"
    CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env CXX=${CMAKE_CXX_COMPILER} CXXFLAGS=${GPP_LD_FLAGS} ../configure ${EXT_CONFIG_OPT}
    BUILD_COMMAND ${MAKE}
    INSTALL_COMMAND ""        # installは不要
    UPDATE_COMMAND  ""        # これを追加するとソースの微細な変化による再構成を防げる
)

# kissat も同様に設定
ExternalProject_Add(ext_kissat
    SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/kissat"
    # ext_cadical と同じ理由で STAMP_DIR を BINARY_DIR 配下に置く。
    STAMP_DIR  "${CMAKE_CURRENT_SOURCE_DIR}/kissat/${EXT_BUILD_SUBDIR}/cmake_stamps"
    BINARY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/kissat/${EXT_BUILD_SUBDIR}"
    CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env CC=${CMAKE_C_COMPILER} CXX=${CMAKE_CXX_COMPILER} CXXFLAGS=${GPP_LD_FLAGS} ../configure ${EXT_CONFIG_OPT}
    BUILD_COMMAND ${MAKE}
    INSTALL_COMMAND ""
    UPDATE_COMMAND  ""        # これを追加するとソースの微細な変化による再構成を防げる
)

#ExternalProject_Add(ext_glueminisat
#    SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/glueminisat"
#    BINARY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/glueminisat/build"
#    CMAKE_ARGS
#        -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
#        -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
#    INSTALL_COMMAND ""        # インストールは不要
#    BUILD_ALWAYS 1            # ソース変更を常に反映したい場合は有効にする
#)

# --- 3. ライブラリの実体パスを定義 ---
set(CADICAL_LIB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/${CADICAL_SRC_DIR}/${EXT_BUILD_SUBDIR}")
set(KISSAT_LIB_DIR  "${CMAKE_CURRENT_SOURCE_DIR}/kissat/${EXT_BUILD_SUBDIR}")

# シンボル競合解決スクリプトの実行ターゲット
add_custom_target(fix_kitten_symbols
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/fix_kitten_symbols.sh ${KISSAT_LIB_DIR} ${CADICAL_LIB_DIR}
    COMMENT "Renaming conflicting symbols in kissat and cadical libraries"
    DEPENDS ext_cadical ext_kissat
)

set(GLUEMINISAT_LIB "${CMAKE_BINARY_DIR}/glueminisat/simp/libglueminisat.a")

# glueminisat
add_subdirectory(glueminisat)

# プロジェクト直下の .C ファイル
file(GLOB PB_SRCS "*.C")
list(REMOVE_ITEM PB_SRCS
    "${CMAKE_CURRENT_SOURCE_DIR}/Main.C"
)
# Main.C
set(MAIN_SRC Main.C)

# ADTs ディレクトリ内の特定のソース
set(ADTS_SRCS ADTs/FEnv.C ADTs/File.C ADTs/Global.C)


# --- ビルドタイプごとの個別設定 ---
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    add_definitions(-DDEBUG -DPARANOID)
    add_compile_options(-O0 -ggdb3)
else()
    # Release系の共通最適化フラグ (MakefileのCOPTIMIZE)
    add_compile_options(-O3 -fomit-frame-pointer -falign-loops=4 -falign-functions=16)
endif()

if(USE_64BIT_INT)
    add_definitions(-DNO_GMP)
endif()

if(USE_CADICAL2)
    add_definitions(-DUSE_CADICAL2)
endif()

# --- 実行ファイルの作成とリンク ---
add_executable(naps ${MAIN_SRC} ${PB_SRCS} ${ADTS_SRCS})

target_link_directories(naps PRIVATE
    ${CADICAL_LIB_DIR}
    ${KISSAT_LIB_DIR}
    /usr/local/lib
    /opt/homebrew/lib
)

# libcadical.a と libkissat.a の共通する関数は最初に見つかったものを使う（リスクあり）
target_link_options(naps PRIVATE -Wl,--allow-multiple-definition)

set(LINK_LIBS ${GLUEMINISAT_LIB})

# 2. 外部ライブラリ (フルパスで指定して再リンクを確実にする)
list(APPEND LINK_LIBS "${CADICAL_LIB_DIR}/libcadical.a" "${KISSAT_LIB_DIR}/libkissat.a" z)

# 3. GMPの設定
if(USE_GMP AND NOT USE_64BIT_INT)
    list(APPEND LINK_LIBS gmp)
endif()

# 4. SCIPの設定
if(USE_SCIP)
    # 静的 SCIP(libscip.a) を使うのは次の2ケース:
    #   1. STATIC_BUILD: 実行ファイルを完全静的リンクする(make rs / rx)。
    #      .so への意図しないフォールバックを避けるため、prefix 未指定なら
    #      scip_static を既定とし、見つからなければエラーにする。
    #   2. SCIP_STATIC_PREFIX の明示指定: Python 拡張(.so)へ PIC 静的リンクする場合など
    #      (setup.py が環境変数 SCIP_STATIC_PREFIX を橋渡しする)。この場合は
    #      STATIC_BUILD ではないため既定 prefix は補わず、未指定なら従来どおり
    #      システムの libscip.so を使う。
    if(STATIC_BUILD AND NOT SCIP_STATIC_PREFIX)
        set(SCIP_STATIC_PREFIX "${CMAKE_SOURCE_DIR}/scip_static")
    endif()

    if(SCIP_STATIC_PREFIX)
        if(NOT EXISTS "${SCIP_STATIC_PREFIX}/lib/cmake/scip/scip-config.cmake")
            message(FATAL_ERROR
                "静的 SCIP が見つかりません: ${SCIP_STATIC_PREFIX}\n"
                "INSTALL.md の手順で SCIP を静的ビルドし ${CMAKE_SOURCE_DIR}/scip_static "
                "にインストールするか、-D SCIP_STATIC_PREFIX=<path> で場所を指定してください。\n"
                "SCIP なしでビルドしたい場合は -D USE_SCIP=OFF を指定してください。")
        endif()
        # scip-config.cmakeがSoPlexへのfind_dependencyを内包するため、
        # 依存ライブラリも連鎖的に解決される。
        find_package(SCIP CONFIG REQUIRED
            PATHS ${SCIP_STATIC_PREFIX}
            NO_DEFAULT_PATH)
        list(APPEND LINK_LIBS libscip)
        add_compile_definitions(USE_SCIP)
    else()
        # 動的リンクではシステムの libscip.so を探す。
        find_library(SCIP_LIBRARY NAMES scip
            PATHS /usr/lib /lib /usr/local/lib
            NO_DEFAULT_PATH)
        if(SCIP_LIBRARY)
            list(APPEND LINK_LIBS ${SCIP_LIBRARY})
            add_compile_definitions(USE_SCIP)
        else()
            message(FATAL_ERROR
                "SCIP library (libscip.so) not found in /usr/lib, /lib, /usr/local/lib.\n"
                "Install scipoptsuite, or specify -D USE_SCIP=OFF to build without SCIP.")
        endif()
    endif()
endif()

# まとめてリンク
target_link_libraries(naps PRIVATE ${LINK_LIBS})

# 静的リンク設定
if(STATIC_BUILD)
	set_target_properties(naps PROPERTIES LINK_FLAGS "--static")
endif()

# napsをビルドする前に外部プロジェクトのビルドを完了させる依存関係を追加
add_dependencies(naps ext_cadical ext_kissat fix_kitten_symbols libglueminisat)

# --- テスト用ターゲットの設定 (FreeBSD以外で有効化) ---
find_package(GTest)

# --- 2. GTest が見つかり、かつ FreeBSD 以外の場合にテストを構成 ---
if(GTest_FOUND AND NOT CMAKE_SYSTEM_NAME MATCHES "FreeBSD")
    message(STATUS "GoogleTest found: Building tests.")

    enable_testing()

    # 共通のテスト設定
    set(TEST_SRCS ${PB_SRCS} ${ADTS_SRCS})

    # sat_solver_test
    add_executable(sat_solver_test tests/sat_solver_test.cpp ${TEST_SRCS})
    target_link_directories(sat_solver_test PRIVATE ${CADICAL_LIB_DIR} ${KISSAT_LIB_DIR})
    target_link_libraries(sat_solver_test PRIVATE ${LINK_LIBS} GTest::gtest GTest::gtest_main)
    add_dependencies(sat_solver_test ext_cadical ext_kissat fix_kitten_symbols libglueminisat)

    # libcadical.a と libkissat.a の共通する関数は最初に見つかったものを使う（リスクあり）
    #    target_link_options(sat_solver_test PRIVATE -Wl,--allow-multiple-definition)

    # pb_solver_test
    add_executable(pb_solver_test tests/pb_solver_test.cpp ${TEST_SRCS})
    target_link_directories(pb_solver_test PRIVATE ${CADICAL_LIB_DIR} ${KISSAT_LIB_DIR})
    target_link_libraries(pb_solver_test PRIVATE ${LINK_LIBS} GTest::gtest GTest::gtest_main)
    add_dependencies(pb_solver_test ext_cadical ext_kissat fix_kitten_symbols libglueminisat)

    # failed_assumptions_test
    add_executable(failed_assumptions_test tests/failed_assumptions_test.cpp ${TEST_SRCS})
    target_link_directories(failed_assumptions_test PRIVATE ${CADICAL_LIB_DIR} ${KISSAT_LIB_DIR})
    target_link_libraries(failed_assumptions_test PRIVATE ${LINK_LIBS} GTest::gtest GTest::gtest_main)
    add_dependencies(failed_assumptions_test ext_cadical ext_kissat fix_kitten_symbols libglueminisat)

    add_test(NAME sat_solver_test COMMAND sat_solver_test WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME pb_solver_test COMMAND pb_solver_test WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
    add_test(NAME failed_assumptions_test COMMAND failed_assumptions_test WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})

    # libcadical.a と libkissat.a の共通する関数は最初に見つかったものを使う（リスクあり）
    # target_link_options(pb_solver_test PRIVATE -Wl,--allow-multiple-definition)
else()
    # 条件を満たさない場合のメッセージ
    if(CMAKE_SYSTEM_NAME MATCHES "FreeBSD")
        message(STATUS "Tests are disabled on FreeBSD due to ABI compatibility.")
    else()
        message(STATUS "GoogleTest NOT found: Skipping tests.")
    endif()
endif()

# --- Python バインディング (BUILD_PYTHON=ON のとき) ---
if(BUILD_PYTHON)
    find_package(pybind11 CONFIG REQUIRED)

    pybind11_add_module(naps_python python/naps_bindings.cpp ${PB_SRCS} ${ADTS_SRCS})
    # import 名は naps（ターゲット名は実行ファイル naps と衝突しないよう naps_python）
    set_target_properties(naps_python PROPERTIES OUTPUT_NAME naps)
    target_link_directories(naps_python PRIVATE
        ${CADICAL_LIB_DIR}
        ${KISSAT_LIB_DIR}
        /usr/local/lib
        /opt/homebrew/lib
    )
    target_link_options(naps_python PRIVATE -Wl,--allow-multiple-definition)
    target_link_libraries(naps_python PRIVATE ${LINK_LIBS})
    add_dependencies(naps_python ext_cadical ext_kissat fix_kitten_symbols libglueminisat)
endif()

