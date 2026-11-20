#!/bin/sh

# export MROOT=$PWD
# make -C simp r
# cp simp/glueminisat-simp_release ./glueminisat

# Releaseビルドの設定
cmake -B build_release -D CMAKE_BUILD_TYPE=Release

# ビルド実行
cmake --build build_release

cp build_release/simp/glueminisat ./glueminisat


 
