import git
import pytest


class RepoBuilder:
    """A throwaway repository, committed to file by file."""

    def __init__(self, path):
        self.path = path
        self.repo = git.Repo.init(path)
        with self.repo.config_writer() as cw:
            cw.set_value("user", "name", "Tester")
            cw.set_value("user", "email", "tester@example.org")
            cw.set_value("core", "autocrlf", "false")

    def write(self, rel, content):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode("utf-8"))
        self.repo.index.add([rel])

    def remove(self, rel):
        self.repo.index.remove([rel], working_tree=True)

    def move(self, old, new):
        self.repo.index.move([old, new])

    def commit(self, message) -> str:
        return self.repo.index.commit(message).hexsha


@pytest.fixture
def builder(tmp_path):
    return RepoBuilder(tmp_path / "repo")


@pytest.fixture
def two_commits(builder):
    builder.write("doc.md", "Hello world.\nSecond line.\n")
    base = builder.commit("first <draft>")
    builder.write("doc.md", "Hello there.\nSecond line.\n<script>x</script>\n")
    target = builder.commit("second")
    return builder, base, target
