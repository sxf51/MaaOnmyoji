"""发布包 Agent 启动入口。

Linux/macOS 包把 Python 依赖放在项目根目录 deps 中，本文件须在导入 main/maa 前
将它加入模块搜索路径。Windows 包使用内置 Python，其 site-packages 已由 ._pth 加载。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = Path(__file__).resolve().parent
DEPS_DIR = PROJECT_ROOT / "deps"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if DEPS_DIR.is_dir():
    sys.path.insert(0, str(DEPS_DIR))


def _log(message: str) -> None:
    debug_dir = PROJECT_ROOT / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    with (debug_dir / "agent-bootstrap.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(UTC).isoformat()} {message}\n")


def run() -> int:
    try:
        os.chdir(PROJECT_ROOT)
        _log(f"bootstrap started: python={sys.executable}, cwd={Path.cwd()}")
        if sys.platform.startswith("linux") and sys.prefix == sys.base_prefix:
            return _prepare_linux_venv()
        from main import main  # noqa: PLC0415

        main()
        _log("agent stopped normally")
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        _log(f"agent exited: code={code}")
        return code
    except BaseException:
        detail = traceback.format_exc()
        _log("agent startup failed:\n" + detail)
        print(detail, file=sys.stderr)
        return 1


def _prepare_linux_venv() -> int:
    """使用随包 wheels 创建 Python 3.13 虚拟环境，然后重新启动 Bootstrap。"""

    python = _find_python_313()
    if python is None:
        _log("Linux Agent requires Python 3.13, but no compatible interpreter was found")
        print("MaaOnmyoji Agent requires Python 3.13", file=sys.stderr)
        return 1

    venv_dir = PROJECT_ROOT / ".venv"
    venv_python = venv_dir / "bin" / "python3"
    if not venv_python.is_file():
        _log(f"creating Linux virtual environment: {venv_dir}")
        subprocess.run([str(python), "-m", "venv", str(venv_dir)], check=True)

    requirements = PROJECT_ROOT / "requirements.txt"
    wheels = PROJECT_ROOT / "deps"
    marker = venv_dir / ".maaonmyoji-requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
        _log("installing Linux Agent dependencies from packaged wheels")
        subprocess.run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheels),
                "--requirement",
                str(requirements),
            ],
            check=True,
        )
        marker.write_text(digest + "\n", encoding="utf-8")

    _log(f"relaunching with Linux virtual environment: {venv_python}")
    result = subprocess.run(
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode


def _find_python_313() -> Path | None:
    if sys.version_info[:2] == (3, 13):
        return Path(sys.executable)
    for name in ("python3.13", "python313"):
        executable = shutil.which(name)
        if executable:
            return Path(executable)
    return None


if __name__ == "__main__":
    raise SystemExit(run())
