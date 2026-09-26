import json
import os
import time

import pytest

from Components import pipeline


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr("Components.config.WORK_DIR", str(tmp_path / "work"))
    os.makedirs(tmp_path / "work")
    return tmp_path


def make_project(work, project_id, title, video_exists=True):
    work_dir = work / "work" / project_id
    os.makedirs(work_dir)
    video = work / f"{project_id}.mp4"
    if video_exists:
        video.write_bytes(b"video")
    project = {"id": project_id, "source": f"https://youtu.be/{project_id}", "title": title, "slug": title,
               "video_path": str(video), "work_dir": str(work_dir), "duration": 90.0, "width": 1280,
               "height": 720, "fps": 30.0, "has_audio": True, "transcript": {"language": "en", "segments": []}}
    (work_dir / "project.json").write_text(json.dumps(project))
    return project


def test_save_session_merges_fields(work):
    project = make_project(work, "aaaaaaaa", "Talk")
    clips = [{"id": 1, "start": 1.0, "end": 9.0, "title": "Edited title", "score": 8}]
    pipeline.save_session(project, clips=clips, settings={"style": "boxed", "min_len": 15})
    pipeline.save_session(project, caption_edits={"1": [[0, 1, "fixed"]]}, settings={"framing": "center"})
    session = pipeline.load_session(project)
    assert session["clips"] == clips
    assert session["caption_edits"] == {"1": [[0, 1, "fixed"]]}
    assert session["settings"] == {"style": "boxed", "min_len": 15, "framing": "center"}
    assert [f for f in os.listdir(project["work_dir"]) if f.endswith(".tmp")] == []


def test_renders_keep_latest_per_clip(work):
    project = make_project(work, "aaaaaaaa", "Talk")
    clip = {"id": 1, "start": 0, "end": 5, "title": "A"}
    for name in ("old.mp4", "new.mp4"):
        video = work / name
        video.write_bytes(b"x")
        pipeline.save_session(project, render_results=[{"clip": clip, "video": str(video), "metadata": "m",
                                                        "thumbnail": "t", "text": "txt", "framing": "track"}])
    renders = pipeline.load_session(project)["renders"]
    assert len(renders) == 1 and renders[0]["video"].endswith("new.mp4")


def test_list_history_newest_first_and_skips_missing_video(work):
    old = make_project(work, "aaaaaaaa", "Old")
    pipeline.save_session(old, clips=[{"id": 1}])
    time.sleep(1.1)
    new = make_project(work, "bbbbbbbb", "New")
    pipeline.save_session(new, clips=[{"id": 1}, {"id": 2}])
    make_project(work, "cccccccc", "Gone", video_exists=False)
    rows = pipeline.list_history()
    assert [r["title"] for r in rows] == ["New", "Old"]
    assert rows[0]["n_clips"] == 2 and rows[0]["n_renders"] == 0


def test_open_project_does_not_download(work, monkeypatch):
    make_project(work, "aaaaaaaa", "Talk")
    monkeypatch.setattr("Components.pipeline.prepare", lambda *a, **k: pytest.fail("must not re-prepare"))
    project, session = pipeline.open_project("aaaaaaaa")
    assert project["title"] == "Talk" and session["clips"] == []


def test_delete_project_rejects_path_tricks_and_keeps_output(work, tmp_path):
    make_project(work, "aaaaaaaa", "Talk")
    for bad in ("../outside", "..", "/etc", "AAAAAAAA", "uploads"):
        with pytest.raises(ValueError):
            pipeline.delete_project(bad)
    output = tmp_path / "output" / "short.mp4"
    os.makedirs(output.parent)
    output.write_bytes(b"short")
    pipeline.delete_project("aaaaaaaa")
    assert not os.path.exists(tmp_path / "work" / "aaaaaaaa")
    assert output.exists()
    assert pipeline.list_history() == []


def test_import_upload_dedupes_identical_files(work, tmp_path):
    a = tmp_path / "gradio_tmp_1" / "clip.mp4"
    b = tmp_path / "gradio_tmp_2" / "clip.mp4"
    for f in (a, b):
        os.makedirs(f.parent)
        f.write_bytes(b"same video bytes")
    first, second = pipeline.import_upload(str(a)), pipeline.import_upload(str(b))
    assert first == second and os.path.exists(first)
    assert os.path.dirname(first) == os.path.join(str(tmp_path / "work"), "uploads")


def test_save_session_after_delete_is_a_noop(work):
    project = make_project(work, "aaaaaaaa", "Talk")
    pipeline.delete_project("aaaaaaaa")
    assert pipeline.save_session(project, clips=[{"id": 1}]) is None
