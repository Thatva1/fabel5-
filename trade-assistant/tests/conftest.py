"""Nothing in the test suite may touch the real trading book.

This exists because it happened. A test that exercised the manual-close
endpoint reached `Book.load()` with its default path — `Book.load` and
`Book.save` take BOOK_PATH as a DEFAULT ARGUMENT, bound when the class was
defined, so the usual `monkeypatch.setattr(book, "BOOK_PATH", tmp)` does not
reach them — and the endpoint closed all 92 live positions and saved the
result. The suite passed. Nothing said a word.

The book is the only record of what the trader has done, and this module's own
docstring already says the failure mode that matters here is CORRUPTED STATE
rather than a wrong number. So the guard is unconditional and global: every
test runs with the book, the closed-trade archive and the scan snapshot pointed
at a temporary directory, and a test that wants the real ones has to say so.

Opt out for a single test with `@pytest.mark.real_data_dir` — there is no
current reason to, and adding one should feel like a decision.
"""
import os
import tempfile

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_data_dir: let this test read and write the project's real data directory")


@pytest.fixture(autouse=True)
def never_the_real_book(request, monkeypatch):
    if "real_data_dir" in request.keywords:
        yield
        return

    from assistant.core import config as config_module
    from assistant.paper import closed_archive
    from assistant.paper.book import Book

    with tempfile.TemporaryDirectory() as tmp:
        book_path = os.path.join(tmp, "paper_book.json")

        # The default arguments themselves, because rebinding the module
        # attribute does not reach a default bound at class-definition time.
        real_load, real_save = Book.load.__func__, Book.save
        monkeypatch.setattr(Book, "load", classmethod(
            lambda cls, path=None: real_load(cls, path or book_path)))
        monkeypatch.setattr(Book, "save", (
            lambda self, path=None, archive_closed=True:
                real_save(self, path or book_path, archive_closed)))

        monkeypatch.setattr(config_module, "DATA_DIR", tmp, raising=False)
        for module, attribute in ((closed_archive, "ARCHIVE_PATH"),
                                  (closed_archive, "CLOSED_PATH")):
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute,
                                    os.path.join(tmp, "closed_trades.jsonl"))
        yield
