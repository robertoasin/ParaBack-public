import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

ROOT = Path(__file__).parent.resolve()


def _read_version() -> str:
    text = (ROOT / "Version.h").read_text()
    m = re.search(r'#define\s+naps_version\s+"([^"]+)"', text)
    if not m:
        raise RuntimeError("Version.h から naps_version を読み取れませんでした")
    return m.group(1)


class CMakeExtension(Extension):
    def __init__(self, name: str):
        super().__init__(name, sources=[])


class CMakeBuild(build_ext):
    def build_extension(self, ext: CMakeExtension) -> None:
        if shutil.which("cmake") is None:
            raise RuntimeError("cmake が見つかりません。先にインストールしてください。")

        import pybind11

        extdir = os.path.abspath(os.path.dirname(self.get_ext_fullpath(ext.name)))
        build_temp = Path(self.build_temp)
        build_temp.mkdir(parents=True, exist_ok=True)

        cmake_args = [
            f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={extdir}",
            # cmake_minimum_required が 3.27 未満のため pybind11 は legacy な
            # PYTHON_EXECUTABLE を見る classic mode になる。Python_EXECUTABLE
            # だけでは無視され、pyenv shim（PATH検索）で見つかった別バージョンの
            # Python にリンクされてしまうため、両方の変数名を明示的に指定する。
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-DPython_EXECUTABLE={sys.executable}",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_PYTHON=ON",
            f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",
        ]

        # 静的 SCIP(PIC ビルド必須) を使う場合は環境変数で prefix を渡す。
        # 未指定なら従来どおりシステムの libscip.so を探す。
        scip_static_prefix = os.environ.get("SCIP_STATIC_PREFIX")
        if scip_static_prefix:
            cmake_args.append(f"-DSCIP_STATIC_PREFIX={scip_static_prefix}")

        subprocess.run(
            ["cmake", "-S", str(ROOT), "-B", str(build_temp), *cmake_args], check=True
        )
        subprocess.run(
            ["cmake", "--build", str(build_temp), "--target", "naps_python", "--parallel"],
            check=True,
        )


# name や description などの静的メタデータは pyproject.toml ([project]) 側に集約。
# version だけは Version.h 由来の動的値のためここで供給する (dynamic = ["version"])。
setup(
    version=_read_version(),
    packages=[],
    ext_modules=[CMakeExtension("naps")],
    cmdclass={"build_ext": CMakeBuild},
    zip_safe=False,
)
