"""A booking made for someone else keeps who booked it and how they are related (shown on the desk's bookings)."""
from services import schedule_bookings as sb


def test_relation_and_booked_by_are_kept_for_someone_else():
    assert sb._on_behalf("Mother", "Rahul Kulkarni", "Sunita Kulkarni") == {"relation": "mother", "booked_by": "Rahul Kulkarni"}


def test_nothing_is_kept_for_a_booking_for_yourself():
    assert sb._on_behalf("self", "Rahul", "Rahul") == {}
    assert sb._on_behalf(None, "Rahul", "Sunita") == {}


def test_booked_by_is_dropped_when_it_is_the_patient_or_does_not_clean_up():
    assert sb._on_behalf("father", "sunita kulkarni", "Sunita Kulkarni") == {"relation": "father"}
    assert sb._on_behalf("son", "12345", "Sunita") == {"relation": "son"}
