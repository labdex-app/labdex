import subprocess
from pathlib import Path

from labdex.plugins.base import Plugin as Base, PluginError


class Plugin(Base):
    def run_write(self, params):
        path = Path(self.settings["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.ctx.inventory.yaml(), encoding="utf-8")
        tmp.replace(path)
        self.log(f"wrote {path}")
        result = {"path": str(path)}
        if self.settings.get("git_commit"):
            result["commit"] = self._commit(path)
        return result

    def _commit(self, path: Path) -> str:
        def git(*args):
            return subprocess.run(["git", "-C", str(path.parent), *args], capture_output=True, text=True)

        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            raise PluginError(f"{path.parent} isn't a git repository")
        git("add", path.name)
        if git("diff", "--cached", "--quiet").returncode == 0:
            self.log("no changes to commit")
            return ""
        author = self.settings.get("git_author") or "labdex <labdex@localhost>"
        r = git("-c", "user.name=labdex", "-c", "user.email=labdex@localhost",
                "commit", "-m", "labdex: inventory update", f"--author={author}")
        if r.returncode != 0:
            raise PluginError(f"git commit failed: {r.stderr.strip()}")
        sha = git("rev-parse", "--short", "HEAD").stdout.strip()
        self.log(f"committed {sha}")
        return sha
