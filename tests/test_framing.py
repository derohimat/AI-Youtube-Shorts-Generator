from Components import framing
from Components.render import _crop_x_expr


def sample(t, *faces):
    # (cx, cy, w, h, conf, mouth_activity)
    return {"t": t, "faces": [(cx, 300, 100, 100, 0.9, act) for cx, act in faces]}


def test_track_holds_position_inside_dead_zone():
    samples = [sample(i * 0.25, (640 + (i % 3) * 10, 1.0)) for i in range(40)]
    keyframes = framing.plan_track(samples, src_w=1920, crop_w=608)
    assert len(keyframes) == 1


def test_track_cuts_to_active_speaker_after_hold():
    samples = [sample(i * 0.25, (400, 5.0 if i < 20 else 0.0), (1500, 0.0 if i < 20 else 5.0)) for i in range(60)]
    keyframes = framing.plan_track(samples, src_w=1920, crop_w=608)
    assert len(keyframes) == 2
    assert keyframes[0][1] == 400 - 304
    assert keyframes[1][1] == 1500 - 304
    assert 4.5 <= keyframes[1][0] <= 6.5


def test_track_ignores_short_glitches():
    samples = [sample(i * 0.25, (400, 1.0)) for i in range(40)]
    samples[10] = sample(2.5, (1500, 1.0))
    assert len(framing.plan_track(samples, src_w=1920, crop_w=608)) == 1


def test_no_faces_centers():
    assert framing.plan_track([{"t": 0, "faces": []}], src_w=1920, crop_w=608) == [(0.0, 656)]


def test_two_speakers_detection():
    samples = [sample(i * 0.25, (400, 0), (1500, 0)) for i in range(10)]
    assert framing._two_speakers(samples, 1920) == [(400.0, 300.0), (1500.0, 300.0)]


def test_crop_expression():
    assert _crop_x_expr([(0.0, 10)]) == "10"
    assert _crop_x_expr([(0.0, 10), (2.5, 20), (4.0, 30)]) == "if(lt(t,2.50),10,if(lt(t,4.00),20,30))"
