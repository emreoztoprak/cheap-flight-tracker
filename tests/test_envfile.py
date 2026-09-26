from cheap_flights.envfile import parse_env, update_env


def test_parse_handles_comments_quotes_and_export():
    text = """
# comment
TELEGRAM_BOT_TOKEN=123:abc
export SMTP_USER=me@example.com
QUOTED="with spaces # not a comment"
SINGLE='it''s raw'
ESCAPED="a\\"b\\\\c"
EMPTY=
"""
    assert parse_env(text) == {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "SMTP_USER": "me@example.com",
        "QUOTED": "with spaces # not a comment",
        "SINGLE": "it''s raw",
        "ESCAPED": 'a"b\\c',
        "EMPTY": "",
    }


def test_update_keeps_other_lines_and_order():
    text = "# my settings\nA=1\nB=2\n"
    result = update_env(text, {"B": "two words", "C": "3", "A": None})
    assert result == '# my settings\nB="two words"\nC=3\n'
    assert parse_env(result) == {"B": "two words", "C": "3"}


def test_values_round_trip():
    tricky = {"X": 'quote " and \\ backslash', "Y": "#hash", "Z": ""}
    assert parse_env(update_env("", tricky)) == tricky
