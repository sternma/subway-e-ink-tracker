from config.config import config
from services.subway_service import SubwayService, TrainArrival


def _arrival(route_id: str, minutes: int, trip_id: str) -> TrainArrival:
    return TrainArrival(
        minutes_until_arrival=minutes,
        arrival_time="07:00 PM",
        arrival_timestamp=1000 + minutes,
        train_id=trip_id,
        route_id=route_id,
    )


def test_shared_nqrw_feed_is_fetched_once(monkeypatch):
    service = SubwayService()
    fetch_calls = []

    class FakeFeed:
        def filter_trips(self, headed_for_stop_id):
            assert headed_for_stop_id == service.station_id
            return ["r12", "r25"]

    def fake_fetch(line_id):
        fetch_calls.append(line_id)
        return FakeFeed()

    processed = {
        "r12": _arrival("R", 12, "r12"),
        "r25": _arrival("R", 25, "r25"),
    }

    monkeypatch.setattr(config, "TRAIN_LINE_1", "R")
    monkeypatch.setattr(config, "TRAIN_LINE_2", "N")
    monkeypatch.setattr(service, "_fetch_feed", fake_fetch)
    monkeypatch.setattr(service, "_process_train", processed.get)
    monkeypatch.setattr(service, "_feed_url", lambda line_id: "nqrw")

    result = service.get_upcoming_trains()

    assert fetch_calls == ["R"]
    assert [t.minutes_until_arrival for t in result.trains] == [12, 25]
    assert {t.route_id for t in result.trains} == {"R"}


def test_distinct_feeds_are_still_fetched_separately(monkeypatch):
    service = SubwayService()
    fetch_calls = []

    class FakeFeed:
        def __init__(self, trips):
            self.trips = trips

        def filter_trips(self, headed_for_stop_id):
            return self.trips

    def fake_fetch(line_id):
        fetch_calls.append(line_id)
        return FakeFeed(["f3"] if line_id == "F" else ["g8"])

    processed = {
        "f3": _arrival("F", 3, "f3"),
        "g8": _arrival("G", 8, "g8"),
    }

    monkeypatch.setattr(service, "_fetch_feed", fake_fetch)
    monkeypatch.setattr(service, "_process_train", processed.get)
    monkeypatch.setattr(service, "_feed_url", lambda line_id: f"url-{line_id}")

    result = service.get_upcoming_trains()

    assert fetch_calls == ["F", "G"]
    assert [(t.route_id, t.minutes_until_arrival) for t in result.trains] == [
        ("F", 3),
        ("G", 8),
    ]
