# フロントエンド用 Makefile
BUILD_BASE=build

.PHONY: d r rs rx clean

all: rs
	cp $(BUILD_BASE)_rs/simp/glueminisat .
	cp $(BUILD_BASE)_rs/simp/libglueminisat.a .
# Debug
d:
	cmake -B $(BUILD_BASE)_debug -D CMAKE_BUILD_TYPE=Debug
	cmake --build $(BUILD_BASE)_debug

# Release
r:
	cmake -B $(BUILD_BASE)_release -D CMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_BASE)_release

# Release Static / Bignum
rs:
	cmake -B $(BUILD_BASE)_rs -D CMAKE_BUILD_TYPE=Release -D STATIC_BUILD=ON
	cmake --build $(BUILD_BASE)_rs

# Release Static / 64bit integer
rx:
	cmake -B $(BUILD_BASE)_rx -D CMAKE_BUILD_TYPE=Release -D STATIC_BUILD=ON -D USE_64BIT_INT=ON
	cmake --build $(BUILD_BASE)_rx

clean:
	rm -rf build_* glueminisat


