from datetime import timedelta

from services import tools, when


def test_a_move_names_the_booking_day_then_the_new_day():
    fri = when.resolve_date("Friday").date
    sat = fri + timedelta(days=1)
    ups = [{"booking_id": "A1", "date": (fri - timedelta(days=3)).isoformat(), "time": "18:00"},
           {"booking_id": "A2", "date": fri.isoformat(), "time": "17:00"}]
    found = {"date": fri.isoformat(), "time": "18:00"}
    for text in ("मेरी शुक्रवार वाली booking शनिवार शाम 6 बजे पर बदल दो", "Can you move my Friday booking to Saturday 6 pm?"):
        mv = tools.move_request(None, text, found, ups)
        assert mv is not None and mv["booking_id"] == "A2" and mv["date"] == sat.isoformat() and mv["time"] == "18:00"


def test_a_named_booking_set_to_a_new_time_is_a_move():
    try:
        from services import when
    except ImportError:  # pragma: no cover
        from backend.services import when
    assert when.says_move("मेरी शुक्रवार वाली booking शनिवार शाम 6 बजे कर दो")
    assert when.says_move("friday wali booking saturday 6 baje kar do")
    assert not when.says_move("मुझे शुक्रवार शाम पाँच बजे का appointment चाहिए")
    assert not when.says_move("हाँ, कर दो")
