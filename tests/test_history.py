"""Stories and histories: parsing forms and well-formedness checks."""

from __future__ import annotations

import pytest

from cyber_detectives import Event, InputError, builtin_map, parse_history, parse_story
from cyber_detectives.history import (check_multi_agent_history, check_single_agent_history,
                                      history_to_string)

M = builtin_map("star_fig2")
EQ2 = [Event("b1", "A"), Event("o1", "A"), Event("o1", "D"), Event("b2", "A"),
       Event("o2", "A"), Event("o2", "D")]


@pytest.mark.parametrize("story", ["ACBAC", "A C B A C", "A,C,B,A,C", " A, C  B,A C ",
                                   ["A", "C", "B", "A", "C"], ("A", "C", "B", "A", "C")])
def test_parse_story_forms(story):
    assert parse_story(story, M) == ["A", "C", "B", "A", "C"]


def test_parse_story_multichar_and_empty():
    assert parse_story("R1 R2") == ["R1", "R2"]
    assert parse_story("") == []
    assert parse_story([]) == []


def test_parse_story_errors():
    with pytest.raises(InputError, match="story element 2 .'D'. is not a room"):
        parse_story("AD", M)
    with pytest.raises(InputError):
        parse_story(["A", 3])
    with pytest.raises(InputError):
        parse_story(5)


@pytest.mark.parametrize("hist", [
    "b1 o1 o1 b2 o2 o2",
    "b1, o1, o1, b2, o2, o2",
    "b1 o1+ o1- b2 o2+ o2-",
    "b1 o1+ o1 b2 o2 o2-",
    ["b1", "o1", "o1", "b2", "o2", "o2"],
    [["b1", "A"], ["o1", "A"], ["o1", "D"], ["b2", "A"], ["o2", "A"], ["o2", "D"]],
    EQ2,
])
def test_parse_history_forms(hist):
    assert parse_history(hist, M) == EQ2


def test_parse_history_toggle_is_per_sensor():
    assert parse_history("o1 o2 o2 o1", M) == [
        Event("o1", "A"), Event("o2", "A"), Event("o2", "D"), Event("o1", "D")]
    # explicit tokens keep the toggle in step
    assert parse_history("o1- o1", M) == [Event("o1", "D"), Event("o1", "A")]


def test_parse_history_without_map():
    assert parse_history("o1+ b1+ o1-") == [Event("o1", "A"), Event("b1", "A"),
                                             Event("o1", "D")]
    assert parse_history([["zz", "A"]]) == [Event("zz", "A")]
    with pytest.raises(InputError, match="a map is needed"):
        parse_history("b1")
    assert parse_history("") == []
    assert parse_history([]) == []


@pytest.mark.parametrize("hist, msg", [
    ("b1 zz", "not a sensor"),
    ("b1-", "only activate"),
    ([["b1", "X"]], "kind must be"),
    ([["zz", "A"]], "not a sensor"),
    ([["b1"]], "not a .sensor, kind. pair"),
    ([["b1", 1]], "two strings"),
    (7, "must be a string or a list"),
])
def test_parse_history_errors(hist, msg):
    with pytest.raises(InputError, match=msg):
        parse_history(hist, M)


def test_parse_history_item_forms():
    # Event, [sensor, kind] (list or tuple) and {"sensor", "kind"} mappings may be mixed
    mixed = [Event("b1", "A"), ("o1", "A"), {"sensor": "o1", "kind": "D"},
             {"kind": "A", "sensor": "b2"}, ["o2", "A"], ["o2", "D"]]
    assert parse_history(mixed, M) == EQ2
    assert parse_history(tuple(EQ2), M) == EQ2
    with pytest.raises(InputError, match=r"history item 1 \(\{'sensor': 'b1'\}\) is not a"):
        parse_history([{"sensor": "b1"}], M)
    with pytest.raises(InputError, match="is not a .sensor, kind. pair"):
        parse_history([{"sensor": "b1", "kind": "A", "x": 1}], M)
    with pytest.raises(InputError, match="kind must be 'A' or 'D'"):
        parse_history([{"sensor": "b1", "kind": "X"}], M)
    with pytest.raises(InputError, match="must hold two strings"):
        parse_history([{"sensor": 1, "kind": "A"}], M)


@pytest.mark.parametrize("hist", [
    {"b1": "A"}, {"sensor": "b1", "kind": "A"}, {}, iter([["b1", "A"]]), {"b1"},
    (x for x in ["b1"]), range(2), True, 1.5, None, b"b1",
])
def test_parse_history_rejects_non_lists(hist):
    # a mapping as the whole history used to be read as its keys
    with pytest.raises(InputError, match="history must be a string or a list of events, got "):
        parse_history(hist, M)
    msg = "history must be a string or a list of events, got %r" % (hist,)
    with pytest.raises(InputError) as e:
        parse_history(hist, M)
    assert str(e.value) == msg


@pytest.mark.parametrize("story", [{"A": 1}, {}, {"A"}, iter("AC"), (x for x in "AC"), True,
                                   b"AC"])
def test_parse_story_rejects_non_lists(story):
    with pytest.raises(InputError) as e:
        parse_story(story, M)
    assert str(e.value) == "story must be a string or a list of room names, got %r" % (story,)


def test_parse_history_check_names_off():
    assert parse_history("b1 zz", M, check_names=False) == [Event("b1", "A"), Event("zz", "A")]
    assert parse_history([["b1", "D"]], M, check_names=False) == [Event("b1", "D")]


def test_history_to_string_roundtrip():
    assert history_to_string(EQ2, M) == "b1 o1 o1 b2 o2 o2"
    weird = [Event("o1", "D"), Event("o1", "A")]
    s = history_to_string(weird, M)
    assert s == "o1- o1+"
    assert parse_history(s, M) == weird
    assert history_to_string(EQ2) == "b1+ o1+ o1- b2+ o2+ o2-"


# ---------------------------------------------------------------- well-formedness


def H(s):
    return parse_history(s, M)


@pytest.mark.parametrize("hist", ["", "b1", "b1 o1 o1 b2 o2 o2", "o1 o1 o1 o1 b2"])
def test_single_well_formed(hist):
    assert check_single_agent_history(M, H(hist)) is None


@pytest.mark.parametrize("hist, msg", [
    ("o1-", "recording 1 .o1 D.: deactivation without"),
    ("b1 o1", "recording 2 .o1 A.: o1 is never deactivated"),
    ("o1 o2 o2 o1", "recording 2 .o2 A. follows recording 1 .o1 A."),
    ("o1 b1 o1", "recording 2 .b1 A. follows"),
    ([["b1", "D"]], "beam detector cannot deactivate"),
])
def test_single_malformed(hist, msg):
    events = H(hist) if isinstance(hist, str) else parse_history(hist, M, check_names=False)
    assert check_single_agent_history(M, events) is not None
    import re
    assert re.search(msg, check_single_agent_history(M, events))


@pytest.mark.parametrize("hist", ["", "b1", "o1 o2 o2 o1", "o1 b1 b2", "o1 o1 o1"])
def test_multi_well_formed(hist):
    assert check_multi_agent_history(M, H(hist)) is None


@pytest.mark.parametrize("hist, msg", [
    ("o1-", "recording 1 .o1 D.: o1 is not active"),
    ("o1+ o1+", "recording 2 .o1 A.: o1 is already active"),
])
def test_multi_malformed(hist, msg):
    import re
    r = check_multi_agent_history(M, H(hist))
    assert r is not None and re.search(msg, r)
