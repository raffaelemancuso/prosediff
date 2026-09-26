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
        self.write_all({rel: content})

    def write_all(self, files):
        """Several files written and staged at once, as {path: content}."""
        for rel, content in files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_bytes(content.encode("utf-8"))
        self.repo.git.add("--", *files)

    def remove(self, rel):
        self.repo.index.remove([rel], working_tree=True)

    def move(self, old, new):
        self.repo.index.move([old, new])

    def commit(self, message) -> str:
        return self.repo.index.commit(message).hexsha


@pytest.fixture
def builder(tmp_path):
    return RepoBuilder(tmp_path / "repo")


def two_versions(builder):
    """doc.md committed twice: a subject and a new version with markup to
    escape."""
    builder.write("doc.md", "Hello world.\nSecond line.\n")
    base = builder.commit("first <draft>")
    builder.write("doc.md", "Hello there.\nSecond line.\n<script>x</script>\n")
    target = builder.commit("second")
    return builder, base, target


@pytest.fixture(scope="module")
def two_commits(tmp_path_factory):
    """two_versions, built once for the module: its tests must not change it."""
    b, base, target = two_versions(RepoBuilder(tmp_path_factory.mktemp("two_commits") / "repo"))
    yield b, base, target
    b.repo.close()


@pytest.fixture(scope="session")
def history(tmp_path_factory):
    """A repository with five commits of doc.md, an uncommitted change and an
    empty folder; built once, as no test changes it."""
    builder = RepoBuilder(tmp_path_factory.mktemp("history") / "repo")
    shas = []
    for k in range(5):
        builder.write("doc.md", f"Version {k} of the text.\n")
        shas.append(builder.commit(f"commit {k}"))
    (builder.path / "doc.md").write_text("Uncommitted version of the text.\n")
    (builder.path / "sub").mkdir()
    yield builder, shas
    builder.repo.close()
