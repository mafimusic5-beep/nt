from __future__ import annotations

import pool_reservation_bridge as bridge


def test_geoip_city_region_code_is_accepted():
    assert bridge._REGION_RE.fullmatch('de-frankfurt-am-main')


def test_region_code_longer_than_backend_limit_is_rejected():
    assert bridge._REGION_RE.fullmatch('a' * 64)
    assert bridge._REGION_RE.fullmatch('a' * 65) is None
