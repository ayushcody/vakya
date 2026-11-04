"""Names in any script are accepted (Devanagari vowel signs are combining marks), junk is not."""
import pytest

from services import schedule_bookings as sb


@pytest.mark.parametrize("name", ["सुनीता कुलकर्णी", "राम", "मराठी  नाव", "Priya Sharma", "O'Brien-Smith Jr.", "Zoë",
                                  "ಕನ್ನಡ ಹೆಸರು", "அருண் குமார்", "Anil K. Patil"])
def test_names_in_any_script_are_accepted(name):
    assert sb.clean_name(name) == " ".join(name.split())


@pytest.mark.parametrize("name", ["12345", "Priya 2", "..", "--x", "a", "", None, "aaaa", "x" * 81, "सु" * 41, "@priya",
                                  "Priya<script>", "९८७६५"])
def test_digits_symbols_and_junk_are_refused(name):
    with pytest.raises(sb.BookingProblem) as e:
        sb.clean_name(name)
    assert e.value.code == "INVALID_NAME"


def test_the_doctor_booking_server_uses_the_same_rule():
    from mcp_servers import appointments as ap
    assert ap._clean_name("सुनीता कुलकर्णी") == "सुनीता कुलकर्णी"
    with pytest.raises(ap.AppointmentError):
        ap._clean_name("12345")
