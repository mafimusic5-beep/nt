from src.backend.services.region_detection_service import DetectedRegion, RegionDetectionService


def test_region_prefers_provider_metadata():
    detector = RegionDetectionService()

    region = detector.detect(
        endpoint="203.0.113.10",
        provider_metadata={"datacenter": "Frankfurt am Main"},
        configured_datacenter="moscow",
    )

    assert region is not None
    assert region.code == "frankfurt"
    assert region.name == "Frankfurt"
    assert region.source == "provider_metadata"


def test_region_uses_endpoint_location_before_configured_datacenter():
    detector = RegionDetectionService()

    region = detector.detect(
        endpoint="ams-01.example.net",
        provider_metadata={},
        configured_datacenter="moscow",
    )

    assert region is not None
    assert region.code == "amsterdam"
    assert region.source == "endpoint_name"


def test_region_uses_ip_geolocation_before_configured_datacenter(monkeypatch):
    detector = RegionDetectionService()
    monkeypatch.setattr(detector, "_resolve_public_ip", lambda endpoint: "8.8.8.8")
    monkeypatch.setattr(
        detector,
        "_from_ip_geo",
        lambda ip: DetectedRegion(code="zurich", name="Zurich", country="Switzerland", source="ip_geolocation", confidence=0.85),
    )

    region = detector.detect(
        endpoint="8.8.8.8",
        provider_metadata={},
        configured_datacenter="moscow",
    )

    assert region is not None
    assert region.code == "zurich"
    assert region.source == "ip_geolocation"


def test_region_falls_back_to_configured_datacenter_when_endpoint_unknown(monkeypatch):
    detector = RegionDetectionService()
    monkeypatch.setattr(detector, "_resolve_public_ip", lambda endpoint: None)

    region = detector.detect(
        endpoint="unknown.example.net",
        provider_metadata={},
        configured_datacenter="Helsinki",
    )

    assert region is not None
    assert region.code == "helsinki"
    assert region.source == "provider_datacenter"
