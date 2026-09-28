import os
import sys
import time
import types

import pytest

from Components import highlights, jobs


def wait(work_dir, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.read(work_dir)
        if job and not jobs.is_active(job):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_job_runs_in_background_and_reports_progress(tmp_path):
    work_dir = str(tmp_path / "aaaaaaaa")
    seen = []

    def fn(progress):
        progress(0.5, "halfway")
        seen.append("ran")

    job = jobs.submit(work_dir, "analyze", fn, {"source": "x"})
    assert job["status"] == "queued"
    done = wait(work_dir)
    assert done["status"] == "done" and done["progress"] == 1.0 and seen == ["ran"]
    assert done["params"] == {"source": "x"}


def test_job_errors_are_recorded(tmp_path):
    work_dir = str(tmp_path / "bbbbbbbb")

    def fn(progress):
        raise ValueError("boom")

    jobs.submit(work_dir, "render", fn)
    job = wait(work_dir)
    assert job["status"] == "error" and "boom" in job["error"]
    assert "gagal" in jobs.describe(job)


def test_only_one_active_job_per_project(tmp_path):
    work_dir = str(tmp_path / "cccccccc")
    import threading
    release = threading.Event()
    jobs.submit(work_dir, "analyze", lambda progress: release.wait(5))
    with pytest.raises(RuntimeError):
        jobs.submit(work_dir, "render", lambda progress: None)
    release.set()
    wait(work_dir)


def test_recover_marks_unfinished_jobs_interrupted(tmp_path):
    work_dir = tmp_path / "dddddddd"
    os.makedirs(work_dir)
    (work_dir / "job.json").write_text('{"kind": "render", "status": "running", "progress": 0.4}')
    assert jobs.recover(str(tmp_path)) == 1
    job = jobs.read(str(work_dir))
    assert job["status"] == "interrupted" and "Coba lagi" in jobs.describe(job)


class _Reply:
    content = "OK"


class _LLM:
    def __init__(self, delay=0.0, fail=None):
        self.delay, self.fail = delay, fail

    def invoke(self, messages):
        if self.fail:
            raise self.fail
        time.sleep(self.delay)
        return _Reply()


def test_connection_ok_error_and_timeout():
    ok, msg = highlights.test_connection("openai", "gpt-4o-mini", llm=_LLM())
    assert ok and '"OK"' in msg
    ok, msg = highlights.test_connection("openai", "x", llm=_LLM(fail=RuntimeError("401 invalid key")))
    assert not ok and "401" in msg
    ok, msg = highlights.test_connection("openai", "x", timeout=0.2, llm=_LLM(delay=1))
    assert not ok and "within" in msg
    assert highlights.test_connection("heuristic")[0]


def test_ytdlp_uses_cookies_and_explains_bot_check(tmp_path, monkeypatch):
    from Components import YoutubeDownloader, config
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(config, "YTDLP_COOKIES", str(cookies))
    captured = {}

    class DownloadError(Exception):
        pass

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            raise DownloadError("ERROR: Sign in to confirm you’re not a bot. Use --cookies-from-browser")

    fake = types.SimpleNamespace(YoutubeDL=FakeYDL, utils=types.SimpleNamespace(DownloadError=DownloadError))
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    with pytest.raises(RuntimeError, match="cookies.txt"):
        YoutubeDownloader._download_ytdlp("https://youtu.be/x", str(tmp_path), 1080)
    assert captured["cookiefile"] == str(cookies)
