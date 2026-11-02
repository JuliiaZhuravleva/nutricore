"""✅ ОК: the buttons leave a finished result, through the real application."""

from __future__ import annotations

import json

from app.services import close_row, feedback_screen, weight_screen
from tests.test_invite import OWNER, Phone, Session, _as, play, say, tap  # noqa: F401


class Stubborn(Phone):
    """A message Telegram will not change: already bare, or too old to edit."""

    async def do_request(self, url, method, request_data=None, **kwargs):
        if url.endswith("/editMessageReplyMarkup"):
            params = dict(request_data.parameters)
            self.calls.append(("refused_edit", params))
            body = {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            }
            return 400, json.dumps(body).encode()
        return await super().do_request(url, method, request_data, **kwargs)


def _calls(wire, name):
    return [params for called, params in wire.calls if called == name]


def test_the_tap_takes_the_buttons_off_that_message_and_nothing_else(
    Session,
):  # noqa: F811
    wire = play(say(OWNER, "/start"), tap(OWNER, close_row.CALLBACK, message_id=4321))
    (edit,) = _calls(wire, "editMessageReplyMarkup")
    assert edit["message_id"] == 4321
    assert "reply_markup" not in edit or not json.loads(edit["reply_markup"]).get(
        "inline_keyboard"
    )
    # The text stays: no edit of the words and no new message for the tap.
    assert _calls(wire, "editMessageText") == []
    assert _calls(wire, "answerCallbackQuery")


def test_a_refused_edit_still_answers_the_tap_and_breaks_nothing(
    Session, caplog
):  # noqa: F811
    wire = play(tap(OWNER, close_row.CALLBACK), wire=Stubborn())
    assert _calls(wire, "refused_edit")  # control: the refusal did happen
    assert _calls(wire, "answerCallbackQuery")
    assert "buttons stayed" in caplog.text
    assert "No error handlers" not in caplog.text


def test_ok_comes_first_on_every_finished_result_with_a_way_back():
    weight = weight_screen.saved_screen(
        type("M", (), {"weight": 72.5, "id": 1})(), None
    )
    for screen in (weight, feedback_screen.sent_screen(1)):
        first, second = screen.markup.inline_keyboard
        assert [b.text for b in first] == [close_row.LABEL]
        assert first[0].callback_data == close_row.CALLBACK
        assert second[0].text.startswith("↩️")
