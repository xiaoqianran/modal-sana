from __future__ import annotations

from pathlib import Path

import pytest

from modal_sana.core.prompts import detect_format, expand_seeds, parse_prompt_file, parse_prompt_text


def test_parse_txt_skips_comments_and_blanks() -> None:
    text = "# title\n\na cat\na dog\n"
    specs = parse_prompt_text(text)
    assert [item.prompt for item in specs] == ["a cat", "a dog"]


def test_parse_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "prompts.jsonl"
    path.write_text('{"prompt":"forest","count":4}\n{"prompt":"city","seed":7}\n', encoding="utf-8")
    specs = parse_prompt_file(path)
    assert specs[0].count == 4
    assert specs[1].seed == 7


def test_parse_json_list(tmp_path: Path) -> None:
    path = tmp_path / "prompts.json"
    path.write_text('[{"prompt":"one"}, "two"]', encoding="utf-8")
    specs = parse_prompt_file(path)
    assert [item.prompt for item in specs] == ["one", "two"]


def test_parse_csv(tmp_path: Path) -> None:
    path = tmp_path / "prompts.csv"
    path.write_text("prompt,count\nhello,3\n", encoding="utf-8")
    specs = parse_prompt_file(path)
    assert specs[0].prompt == "hello"
    assert specs[0].count == 3


def test_expand_seeds() -> None:
    assert expand_seeds(10, 3) == [10, 11, 12]


def test_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "prompts.md"
    path.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError):
        detect_format(path)
