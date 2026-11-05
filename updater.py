from Scripts import github
from Scripts import resource_fetcher
from Scripts import utils
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile


# 自动更新所指向的仓库（汉化版仓库，避免更新后被上游英文版覆盖）
UPDATE_REPO_OWNER = "ZoomBili"
UPDATE_REPO_NAME = "OpCore-Simplify-CN"


class UpdateRecoveryError(RuntimeError):
    """恢复不完整，应用程序必须停止。"""


class Updater:
    update_steps = (
        "下载更新包",
        "校验并解压文件",
        "备份应用程序文件",
        "安装应用程序文件",
    )

    def __init__(self):
        self.root = Path(__file__).resolve().parent
        self.github = github.Github()
        self.fetcher = resource_fetcher.ResourceFetcher()
        self.utils = utils.Utils()

    def is_git_checkout(self):
        return any(os.path.lexists(parent / ".git") for parent in (self.root, *self.root.parents))

    def get_current_sha_version(self):
        path = self.root / "sha_version.txt"
        if not path.exists():
            return None
        sha = path.read_text(encoding="utf-8").strip()
        return sha.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else None

    @staticmethod
    def _application_path(name):
        parts = PurePosixPath(name).parts
        if (not parts or "/".join(parts) != name or re.search(r'[<>:"\\|?*\x00-\x1f]', name)
                or any(part in (".", "..")
                       or part.endswith((".", " "))
                       or re.match(r"(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\.|$)", part)
                       for part in parts)):
            raise ValueError("不安全的更新路径：{}".format(name))
        return (len(parts) > 1 and parts[0] == "Scripts"
                and not any(part.startswith(".") or part in ("__pycache__", "OCK_Files", "Results") for part in parts)
                or name in ("OpCore-Simplify.py", "OpCore-Simplify.bat", "OpCore-Simplify.command",
                            "updater.py", "README.md", "LICENSE"))

    def _destination(self, name):
        path = self.root / name
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ValueError("更新目标为链接：{}".format(name))
        if self.root not in path.resolve().parents:
            raise ValueError("更新目标位于程序目录之外：{}".format(name))
        if path.exists() and not path.is_file():
            raise ValueError(
                "更新目标不是文件：{}".format(name))
        return path

    @staticmethod
    def _hash(path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    def download_update(self, sha, workspace):
        archive_path = workspace / "update.zip"
        url = "https://github.com/{}/{}/archive/{}.zip".format(
            UPDATE_REPO_OWNER, UPDATE_REPO_NAME, sha)
        self.utils.progress_bar("更新 OpCore Simplify", self.update_steps, 0)
        if not self.fetcher.download_and_save_file(url, str(archive_path)):
            raise RuntimeError("无法下载更新包。")
        staged = workspace / "staged"
        files = {}
        seen = set()
        prefix = "{}-{}/".format(UPDATE_REPO_NAME, sha)
        self.utils.progress_bar("更新 OpCore Simplify", self.update_steps, 1)
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 512 * 1024 * 1024:
                raise ValueError("更新包超出支持的大小。")
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise ValueError("更新包路径不规范。")
                if not entry.filename.startswith(prefix):
                    raise ValueError("更新包根目录异常。")
                if entry.is_dir():
                    continue
                name = entry.filename[len(prefix):]
                if not self._application_path(name):
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(
                        "更新包包含符号链接。")
                if name.casefold() in seen:
                    raise ValueError("重复的更新路径：{}".format(name))
                seen.add(name.casefold())
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                if name.endswith(".command"):
                    target.chmod(0o755)
                files[name] = self._hash(target)
        for required in ("OpCore-Simplify.py", "updater.py", "Scripts/github.py", "Scripts/utils.py"):
            if required not in files:
                raise ValueError(
                    "更新包不完整：缺少 {}".format(required))
        return files

    def update_files(self, workspace, sha, files):
        if self.is_git_checkout():
            raise RuntimeError(
                "检测到 Git 检出。请使用 Git 更新此安装。")
        manifest_name = "update_manifest.json"
        manifest_path = self._destination(manifest_name)
        previous = json.loads(manifest_path.read_text(
            encoding="utf-8")) if manifest_path.exists() else {}
        if not isinstance(previous, dict):
            raise ValueError("已安装文件清单无效。")
        for name, digest in previous.items():
            if not self._application_path(name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("已安装文件清单条目无效。")
            path = self._destination(name)
            if not path.exists() or self._hash(path) != digest:
                raise ValueError(
                    "应用程序文件已在本地被修改：{}。请手动更新。".format(name))

        staged = workspace / "staged"
        (staged / manifest_name).write_text(json.dumps(files, indent=2), encoding="utf-8")
        (staged / "sha_version.txt").write_text(sha + "\n", encoding="utf-8")
        names = sorted(set(previous) | set(files)) + \
            [manifest_name, "sha_version.txt"]
        backup = workspace / "backup"
        existed = {}
        self.utils.progress_bar("更新 OpCore Simplify", self.update_steps, 2)
        for name in names:
            path = self._destination(name)
            if manifest_path.exists() and name in files and name not in previous and path.exists():
                raise ValueError(
                    "未受管理的文件将被覆盖：{}。请手动更新。".format(name))
            existed[name] = path.exists()
            if path.exists():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        (workspace / "original_files.json").write_text(json.dumps(existed,
                                                                  indent=2), encoding="utf-8")
        changed = []
        try:
            self.utils.progress_bar(
                "更新 OpCore Simplify", self.update_steps, 3)
            for name in names:
                destination = self._destination(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = staged / name
                changed.append(name)
                if source.exists():
                    os.replace(source, destination)
                else:
                    destination.unlink()
        except (Exception, KeyboardInterrupt) as error:
            failures = []
            for name in reversed(changed):
                try:
                    destination = self._destination(name)
                    if existed[name]:
                        if not destination.exists() or self._hash(destination) != self._hash(backup / name):
                            shutil.copy2(backup / name, destination)
                    else:
                        destination.unlink(missing_ok=True)
                except (Exception, KeyboardInterrupt) as restore_error:
                    failures.append("{}: {}".format(name, restore_error))
            if failures:
                raise UpdateRecoveryError("回滚未完成。备份和原始文件列表已保留在 {}。{}".format(
                    workspace, "; ".join(failures))) from error
            raise RuntimeError(
                "更新失败；原始文件已恢复：{}".format(error)) from error

    def run_update(self):
        if self.is_git_checkout():
            return "skipped"
        self.utils.head("检查更新")
        print("")
        workspace = self.root / ".opcore-update"
        if os.path.lexists(workspace):
            print(
                "\033[91m存在未完成的更新，需要先恢复才能继续运行程序。\033[0m")
            print("  恢复文件：{}".format(workspace))
            print("")
            self.utils.request_input("按回车键退出...")
            return "recovery_required"
        try:
            current = self.get_current_sha_version()
            print("正在检查更新...")
            latest = self.github.get_commits(
                UPDATE_REPO_OWNER, UPDATE_REPO_NAME)
        except Exception as error:
            print(
                "\033[1;93m无法检查更新。您可以继续使用已安装的版本。\033[0m")
            print("  {}".format(error))
            print("")
            self.utils.request_input()
            return "failed"
        sha = latest["sha"]
        print("")
        print("  当前版本：{}".format(
            current[:7] if current else "未知"))
        print("  最新版本：{}".format(sha[:7]))
        print("")
        if current == sha:
            print("您已经在使用最新版本。")
            return "unchanged"
        print("\033[1;96m有可用更新。\033[0m" if current else
              "\033[1;93m无法识别已安装的版本。\033[0m")
        if not (self.root / "update_manifest.json").exists():
            print("更新将替换应用程序文件，包括任何本地修改。")
        print("")
        created = False
        keep_backup = False
        try:
            workspace.mkdir()
            created = True
            files = self.download_update(sha, workspace)
            self.update_files(workspace, sha, files)
        except UpdateRecoveryError as error:
            keep_backup = True
            self.utils.head("更新失败")
            print("")
            print("\033[91m{}\033[0m".format(error))
            print("")
            self.utils.request_input("按回车键退出...")
            return "recovery_required"
        except (Exception, KeyboardInterrupt) as error:
            self.utils.head("更新失败")
            print("")
            print("\033[91m更新未安装：{}\033[0m".format(error))
            print("")
            if not created and os.path.lexists(workspace):
                self.utils.request_input("按回车键退出...")
                return "recovery_required"
            print("您可以继续使用已安装的版本。")
            print("")
            self.utils.request_input()
            return "failed"
        finally:
            if created and not keep_backup:
                try:
                    shutil.rmtree(workspace)
                except OSError as error:
                    print("无法删除更新工作区 {}：{}".format(
                        workspace, error))
        self.utils.progress_bar(
            "更新 OpCore Simplify", self.update_steps, len(self.update_steps), done=True)
        print("\033[92m更新成功完成！\033[0m")
        print("")
        print("  先前版本：{}".format(
            current[:7] if current else "未知"))
        print("  已安装版本：{}".format(sha[:7]))
        if workspace.exists():
            print("\033[1;93m无法删除临时更新文件：{}\033[0m".format(
                workspace))
        print("")
        self.utils.request_input("按回车键重启...")
        return "updated"
