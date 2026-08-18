from pathlib import Path

import json
import os
import hashlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

try:
    import jsonc
except ModuleNotFoundError as e:
    raise ImportError(
        "Missing dependency 'json-with-comments' (imported as 'jsonc').\n"
        f"Install it with:\n  {sys.executable} -m pip install json-with-comments\n"
        "Or add it to your project's requirements."
    ) from e

from configure import configure_ocr_model


working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("install")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"

# the first parameter is self name
if sys.argv.__len__() < 4:
    print("Usage: python install.py <version> <os> <arch>")
    print("Example: python install.py v1.0.0 win x86_64")
    sys.exit(1)

os_name = sys.argv[2]
arch = sys.argv[3]

PYTHON_EMBED_VERSION = "3.13.14"
PROJECT_EXECUTABLE_NAME = "MaaOnmyoji"


def get_dotnet_platform_tag():
    """自动检测当前平台并返回对应的dotnet平台标签"""
    if os_name == "win" and arch == "x86_64":
        platform_tag = "win-x64"
    elif os_name == "win" and arch == "aarch64":
        platform_tag = "win-arm64"
    elif os_name == "macos" and arch == "x86_64":
        platform_tag = "osx-x64"
    elif os_name == "macos" and arch == "aarch64":
        platform_tag = "osx-arm64"
    elif os_name == "linux" and arch == "x86_64":
        platform_tag = "linux-x64"
    elif os_name == "linux" and arch == "aarch64":
        platform_tag = "linux-arm64"
    else:
        print("Unsupported OS or architecture.")
        print("available parameters:")
        print("version: e.g., v1.0.0")
        print("os: [win, macos, linux, android]")
        print("arch: [aarch64, x86_64]")
        sys.exit(1)

    return platform_tag


def install_deps():
    if not (working_dir / "deps" / "bin").exists():
        print('Please download the MaaFramework to "deps" first.')
        print('请先下载 MaaFramework 到 "deps"。')
        sys.exit(1)

    if os_name == "android":
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path,
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
    else:
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path / "runtimes" / get_dotnet_platform_tag() / "native",
            ignore=shutil.ignore_patterns(
                "*MaaDbgControlUnit*",
                "*MaaThriftControlUnit*",
                "*MaaRpc*",
                "*MaaHttp*",
                "plugins",
                "*.node",
                "*MaaPiCli*",
            ),
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "libs" / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "bin" / "plugins",
            install_path / "plugins" / get_dotnet_platform_tag(),
            dirs_exist_ok=True,
        )



def install_resource():

    configure_ocr_model()

    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    # interface.json 中的 import 和 languages 路径均相对于发布包根目录。
    # 这些 PI 配置也是运行时资源，不能只复制 MaaFramework 的 resource 目录。
    for directory in ("tasks", "language"):
        shutil.copytree(
            working_dir / "assets" / directory,
            install_path / directory,
            dirs_exist_ok=True,
        )
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = jsonc.load(f)

    interface["version"] = version

    if os_name == "win":
        interface["agent"] = {
            "child_exec": "./python/python.exe",
            "child_args": ["-u", "./agent/bootstrap.py"],
        }
    elif os_name == "macos":
        interface["agent"] = {
            "child_exec": "./python/bin/python3",
            "child_args": ["-u", "./agent/bootstrap.py"],
        }
    elif os_name == "linux":
        interface["agent"] = {
            "child_exec": "python3",
            "child_args": ["-u", "./agent/bootstrap.py"],
        }
    else:
        # Android 发布包无法启动桌面 Python Agent 子进程。
        interface.pop("agent", None)

    missing_interface_files = []
    for relative_path in interface.get("import", []):
        if not (install_path / relative_path).is_file():
            missing_interface_files.append(relative_path)
    for relative_path in interface.get("languages", {}).values():
        if not (install_path / relative_path).is_file():
            missing_interface_files.append(relative_path)
    if missing_interface_files:
        raise FileNotFoundError(
            "Missing files referenced by interface.json in release package: "
            + ", ".join(missing_interface_files)
        )

    # PI 的 icon 路径相对于项目根目录。若配置了图标，则一并放入发布包。
    icon = interface.get("icon")
    if isinstance(icon, str) and icon:
        source_icon = working_dir / icon
        if not source_icon.is_file():
            raise FileNotFoundError(f"Configured interface icon not found: {source_icon}")
        target_icon = install_path / icon
        target_icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_icon, target_icon)

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        jsonc.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    shutil.copy2(
        working_dir / "README.md",
        install_path,
    )
    shutil.copy2(
        working_dir / "LICENSE",
        install_path,
    )
    shutil.copy2(
        working_dir / "requirements.txt",
        install_path,
    )


def configure_mfa_update_source():
    """让发布包默认使用 GitHub，同时允许用户在设置中切换到 Mirror 酱。

    MFAAvalonia 的 DownloadSourceIndex 默认值为 1（Mirror 酱），0 才是 GitHub。
    这里只修改默认更新源，不移除 Mirror 酱功能；版本检测保持开启，自动更新默认关闭。
    """

    config_path = install_path / "config" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {}
    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            config = jsonc.load(f)
        if not isinstance(config, dict):
            config = {}

    config.update(
        {
            "DownloadSourceIndex": 0,
            "EnableCheckVersion": True,
            "EnableAutoUpdateResource": False,
            "EnableAutoUpdateMFA": False,
        }
    )

    with open(config_path, "w", encoding="utf-8") as f:
        jsonc.dump(config, f, ensure_ascii=False, indent=4)


def rename_gui_entrypoint():
    """将通用 MFA 入口重命名为项目名。

    interface.json 的 name/title 只控制 PI 和窗口显示，不会改变磁盘文件名。
    """

    if os_name == "android":
        return

    suffix = ".exe" if os_name == "win" else ""
    target = install_path / f"{PROJECT_EXECUTABLE_NAME}{suffix}"
    candidates = [
        install_path / f"MFAAvalonia{suffix}",
        install_path / ("MFAAvalonia" if suffix else "MFAAvalonia.exe"),
    ]

    for source in candidates:
        if source.is_file():
            if target.exists():
                target.unlink()
            source.rename(target)
            print(f"Renamed GUI entrypoint: {source.name} -> {target.name}")
            return

    raise FileNotFoundError(
        "MFAAvalonia entrypoint not found; checked: "
        + ", ".join(str(path) for path in candidates)
    )


def install_agent():
    if os_name == "android":
        return

    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        dirs_exist_ok=True,
    )

    requirements = working_dir / "requirements.txt"
    if os_name == "win":
        install_windows_python(requirements)
    elif os_name == "macos":
        install_macos_python(requirements)
    else:
        download_linux_wheels(requirements, install_path / "deps")


def install_python_dependencies(requirements: Path, target: Path):
    """使用构建机同平台 Python 将 Agent 依赖安装到发布目录。"""

    target.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        command = [
            uv,
            "pip",
            "install",
            "--target",
            str(target),
            "--requirements",
            str(requirements),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "--target",
            str(target),
            "--requirement",
            str(requirements),
        ]
    subprocess.run(command, check=True)


def install_windows_python(requirements: Path):
    """安装 Windows embeddable Python，并写入 site-packages。"""

    embed_arch = "arm64" if arch == "aarch64" else "amd64"
    archive_name = f"python-{PYTHON_EMBED_VERSION}-embed-{embed_arch}.zip"
    archive_url = f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/{archive_name}"
    cache_dir = working_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / archive_name
    if not archive_path.is_file():
        print(f"Downloading embedded Python: {archive_url}")
        urllib.request.urlretrieve(archive_url, archive_path)

    python_dir = install_path / "python"
    if python_dir.exists():
        shutil.rmtree(python_dir)
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(python_dir)

    pth_files = list(python_dir.glob("python*._pth"))
    if len(pth_files) != 1:
        raise RuntimeError(f"Expected one embedded Python ._pth file, found: {pth_files}")
    pth_file = pth_files[0]
    pth_lines = pth_file.read_text(encoding="utf-8").splitlines()
    for search_path in (".", "Lib", "Lib/site-packages", "DLLs"):
        if search_path not in pth_lines:
            pth_lines.append(search_path)
    pth_lines = [
        "import site" if line.strip().replace(" ", "") == "#importsite" else line
        for line in pth_lines
    ]
    pth_file.write_text("\n".join(pth_lines) + "\n", encoding="utf-8")

    install_python_dependencies(requirements, python_dir / "Lib" / "site-packages")


def download_linux_wheels(requirements: Path, target: Path):
    """下载 Linux 目标架构 wheels，供 Bootstrap 离线创建虚拟环境。"""

    target.mkdir(parents=True, exist_ok=True)
    platform_tags = (
        ["manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64", "linux_aarch64"]
        if arch == "aarch64"
        else ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "linux_x86_64"]
    )
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--requirement",
        str(requirements),
        "--dest",
        str(target),
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        "--implementation",
        "cp",
        "--abi",
        "cp313",
    ]
    for tag in platform_tags:
        command.extend(["--platform", tag])
    subprocess.run(command, check=True)


def install_macos_python(requirements: Path):
    """安装 python-build-standalone macOS 运行时。"""

    triple = "aarch64-apple-darwin" if arch == "aarch64" else "x86_64-apple-darwin"
    request = urllib.request.Request(
        "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **(
                {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
                if os.environ.get("GITHUB_TOKEN")
                else {}
            ),
        },
    )
    with urllib.request.urlopen(request) as response:
        release = json.load(response)

    tag = release["tag_name"]
    prefix = f"cpython-3.13."
    suffix = f"+{tag}-{triple}-install_only_stripped.tar.gz"
    asset = next(
        (
            item
            for item in release.get("assets", [])
            if item.get("name", "").startswith(prefix) and item.get("name", "").endswith(suffix)
        ),
        None,
    )
    if not asset:
        raise RuntimeError(f"No Python 3.13 standalone runtime found for {triple}")

    cache_dir = working_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / asset["name"]
    if not archive_path.is_file():
        print(f"Downloading standalone Python: {asset['browser_download_url']}")
        urllib.request.urlretrieve(asset["browser_download_url"], archive_path)
    digest = str(asset.get("digest", ""))
    if digest.startswith("sha256:"):
        actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        expected = digest.removeprefix("sha256:").lower()
        if actual != expected:
            archive_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Standalone Python checksum mismatch: expected {expected}, got {actual}"
            )

    python_dir = install_path / "python"
    if python_dir.exists():
        shutil.rmtree(python_dir)
    python_dir.mkdir(parents=True)
    extract_standalone_python(archive_path, python_dir)

    python_exe = python_dir / "bin" / "python3"
    if not python_exe.is_file():
        candidate = find_macos_python_executable(python_dir / "bin")
        if candidate is None:
            raise RuntimeError("Standalone Python executable was not found")
        shutil.copy2(candidate, python_exe)
    python_exe.chmod(python_exe.stat().st_mode | 0o755)
    wheelhouse = download_native_wheels(requirements)
    install_requirements_into_python(python_exe, requirements, wheelhouse)


def find_macos_python_executable(bin_dir: Path) -> Path | None:
    """查找真正的 Python 解释器，排除 python*-config 等辅助脚本。"""

    for name in ("python3.13", "python3.13t", "python"):
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate

    candidates = sorted(
        path
        for path in bin_dir.glob("python3.*")
        if path.is_file() and path.name.removeprefix("python3.").isdigit()
    )
    return candidates[0] if candidates else None


def extract_standalone_python(archive_path: Path, target: Path):
    """去除归档顶层 python/install 目录。"""

    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        roots = {member.name.split("/", 1)[0] for member in members if member.name}
        strip_root = len(roots) == 1 and all("/" in member.name for member in members)
        for member in members:
            name = member.name.replace("\\", "/")
            if strip_root:
                name = name.split("/", 1)[1]
            for prefix in ("python/", "install/"):
                if name.startswith(prefix):
                    name = name[len(prefix) :]
            if not name or ".." in Path(name).parts or name.startswith("/"):
                continue
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            destination.chmod(member.mode & 0o777)


def download_native_wheels(requirements: Path) -> Path:
    """下载当前构建平台的 wheels，并缓存供嵌入式 Python 离线安装。"""

    wheelhouse = working_dir / ".cache" / "python-wheels" / f"{os_name}-{arch}"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    marker = wheelhouse / ".requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
        if any(wheelhouse.glob("*.whl")):
            print(f"Using cached Python wheels: {wheelhouse}")
            return wheelhouse

    for old_wheel in wheelhouse.glob("*.whl"):
        old_wheel.unlink()

    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--requirement",
        str(requirements),
        "--dest",
        str(wheelhouse),
        "--only-binary=:all:",
        "--retries",
        "8",
        "--timeout",
        "120",
    ]
    subprocess.run(command, check=True)
    marker.write_text(digest + "\n", encoding="utf-8")
    return wheelhouse


def install_requirements_into_python(
    python_exe: Path, requirements: Path, wheelhouse: Path
):
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to populate the embedded Python runtime")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python_exe),
            "--system",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--requirements",
            str(requirements),
        ],
        check=True,
    )


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    configure_mfa_update_source()
    install_agent()
    rename_gui_entrypoint()

    print(f"Install to {install_path} successfully.")
