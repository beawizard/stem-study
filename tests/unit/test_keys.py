"""Unit tests for key helpers."""

from app import keys


def test_user_keys():
    assert keys.user_pk("u1") == "USER#u1"
    assert keys.user_meta_sk() == "META"
    assert keys.task_sk("t1") == "TASK#t1"
    assert keys.progress_sk("math", "l1") == "PROGRESS#math#L#l1"
    assert keys.session_sk("s1") == "SESSION#s1"
    assert keys.attempt_sk("s1", "q1") == "ATTEMPT#s1#Q#q1"
    assert keys.payment_sk("p1") == "PAYMENT#p1"


def test_subject_keys():
    assert keys.subject_pk("math") == "SUBJECT#math"
    assert keys.level_sk("l1") == "LEVEL#l1"
    assert keys.question_sk("l1", "q9") == "LEVEL#l1#Q#q9"
