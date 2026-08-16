import pytest
from hotpepperpodcast.parser import ScriptParseError, assign_unlabeled, parse_text

def test_labeled_script():
    parsed = parse_text("Host: Hello\n\n# note\nGuest: Hi")
    assert parsed.speaker_names == ("Host", "Guest")
    assert not parsed.is_ambiguous

def test_ambiguous_script_requires_resolution():
    parsed = parse_text("First line\nSecond line")
    assert parsed.is_ambiguous
    with pytest.raises(ScriptParseError):
        assign_unlabeled(parsed, "ask")

def test_narrator_assignment():
    parsed = assign_unlabeled(parse_text("First\nSecond"), "narrator")
    assert [line.speaker for line in parsed.lines] == ["Narrator", "Narrator"]

def test_alternating_assignment():
    parsed = assign_unlabeled(parse_text("First\nSecond\nThird"), "alternate")
    assert [line.speaker for line in parsed.lines] == ["Speaker 1", "Speaker 2", "Speaker 1"]

def test_empty_rejected():
    with pytest.raises(ScriptParseError):
        parse_text("# only a comment")
