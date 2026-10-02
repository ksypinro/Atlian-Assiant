import pytest

from tools import docgen


def good(self, key: str, *, limit: int = 5, backend=None):
    """Fetch a thing.

    When to use:
        Always.

    Args:
        key: The key.
        limit: Max.
        backend: Force one.

    Returns:
        Thing

    Raises:
        NotFoundError: Missing.

    Access: READ

    Example:
        >>> x = await client.jira.issues.get("PROJ-1")
    """


def test_a_complete_docstring_passes():
    assert docgen.lint_docstring("x.good", good) == []


def test_each_missing_section_is_reported():
    for section in docgen.REQUIRED:
        def broken(self, key: str):
            pass
        broken.__doc__ = good.__doc__.replace(section, "Removed:")
        broken.__doc__ = broken.__doc__.replace("limit: Max.", "").replace("backend: Force one.", "")
        problems = [str(i) for i in docgen.lint_docstring("x.broken", broken)]
        assert any(section in p for p in problems), (section, problems)


def test_undocumented_parameters_and_missing_docstring_are_reported():
    def undocumented(self, key: str, extra: int = 1):
        """Summary.

        When to use:
            x
        Args:
            key: k
        Returns:
            y
        Raises:
            z
        Access: READ
        Example:
            >>> pass
        """
    problems = " ".join(str(i) for i in docgen.lint_docstring("x", undocumented))
    assert "'extra'" in problems and "'key'" not in problems

    def none(self): ...
    assert "missing docstring" in str(docgen.lint_docstring("x", none)[0])


def test_summary_line_must_be_a_sentence():
    def bad(self):
        """Args:

        When to use:
            x
        """
    assert any("summary" in str(i) for i in docgen.lint_docstring("x", bad))


def test_examples_are_extracted():
    assert docgen.examples(good) == ['x = await client.jira.issues.get("PROJ-1")']
    assert docgen.examples(lambda: None) == []


@pytest.mark.anyio
async def test_example_runner_accepts_valid_calls_and_flags_wrong_signatures():
    client = docgen.build_fake_client()
    assert await docgen.run_example(client, 'x = await client.jira.issues.get("PROJ-1")') is None
    assert await docgen.run_example(client, 'x = await client.jira.issues.get("PROJ-404")') is None      # valid shape, missing data
    assert "TypeError" in await docgen.run_example(client, "x = await client.jira.issues.get()")
    assert "AttributeError" in await docgen.run_example(client, "x = await client.jira.nope.get(1)")
    assert await docgen.run_example(client, 'async for i in client.jira.issues.iter_search("x", page_size=1):\n    break') is None


def test_the_whole_facade_passes_lint_and_examples_run():
    assert docgen.main(["--lint"]) == 0
