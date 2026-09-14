from ordigovernance.runtime.patterns.scripted_beat import ScriptedBeat


def test_failing_position_clamped_and_disabled():
    beat = ScriptedBeat(failing_position_index=3)
    assert beat.failing_position(6) == 3
    assert beat.failing_position(2) == 2
    assert ScriptedBeat(failing_position_index=0).failing_position(5) == -1
    assert beat.failing_position(0) == -1


def test_should_fail_once_per_run():
    beat = ScriptedBeat()
    assert beat.should_fail_once(True, "k1") is True
    assert beat.should_fail_once(True, "k1") is False
    assert beat.should_fail_once(False, "k2") is False


def test_score_sequence_repeats_last():
    beat = ScriptedBeat(score_sequence=(62, 88))
    assert beat.score(1) == 62
    assert beat.score(2) == 88
    assert beat.score(3) == 88
    assert ScriptedBeat().score(1, default="d") == "d"


def test_is_pass():
    beat = ScriptedBeat(pass_threshold=80)
    assert beat.is_pass(88) is True
    assert beat.is_pass(62) is False
    assert ScriptedBeat().is_pass("ok") is True