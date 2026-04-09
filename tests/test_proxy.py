"""Tests for scraper/proxy.py — ProxyManager rotation and dead-proxy tracking."""

import pytest

from scraper.proxy import ProxyManager, DEAD_THRESHOLD, _mask_proxy

PROXIES = [
    "http://user:pass@proxy1:8080",
    "http://user:pass@proxy2:8080",
    "http://user:pass@proxy3:8080",
]


class TestGetProxy:
    def test_returns_proxy_from_list(self):
        pm = ProxyManager(PROXIES)
        proxy = pm.get_proxy()
        assert proxy in PROXIES

    def test_rotates_round_robin(self):
        pm = ProxyManager(PROXIES)
        seen = {pm.get_proxy() for _ in range(len(PROXIES))}
        assert seen == set(PROXIES)

    def test_empty_list_returns_none(self):
        pm = ProxyManager([])
        assert pm.get_proxy() is None

    def test_single_proxy_always_returned(self):
        pm = ProxyManager(["http://only:8080"])
        assert pm.get_proxy() == "http://only:8080"
        assert pm.get_proxy() == "http://only:8080"


class TestMarkDead:
    def test_proxy_marked_dead_after_threshold(self):
        pm = ProxyManager(PROXIES)
        proxy = PROXIES[0]
        for _ in range(DEAD_THRESHOLD):
            pm.mark_dead(proxy)
        assert pm.dead_count == 1

    def test_dead_proxy_not_returned(self):
        pm = ProxyManager([PROXIES[0], PROXIES[1]])
        for _ in range(DEAD_THRESHOLD):
            pm.mark_dead(PROXIES[0])
        # All subsequent calls should return only PROXIES[1]
        for _ in range(5):
            assert pm.get_proxy() == PROXIES[1]

    def test_all_proxies_dead_returns_none(self):
        pm = ProxyManager(PROXIES[:2])
        for proxy in PROXIES[:2]:
            for _ in range(DEAD_THRESHOLD):
                pm.mark_dead(proxy)
        assert pm.get_proxy() is None

    def test_dead_count_increments(self):
        pm = ProxyManager(PROXIES)
        for proxy in PROXIES[:2]:
            for _ in range(DEAD_THRESHOLD):
                pm.mark_dead(proxy)
        assert pm.dead_count == 2

    def test_below_threshold_not_dead(self):
        pm = ProxyManager(PROXIES)
        for _ in range(DEAD_THRESHOLD - 1):
            pm.mark_dead(PROXIES[0])
        assert pm.dead_count == 0


class TestMarkAlive:
    def test_recovers_dead_proxy(self):
        pm = ProxyManager([PROXIES[0]])
        for _ in range(DEAD_THRESHOLD):
            pm.mark_dead(PROXIES[0])
        assert pm.dead_count == 1
        pm.mark_alive(PROXIES[0])
        assert pm.dead_count == 0

    def test_alive_proxy_returned_again_after_recovery(self):
        pm = ProxyManager([PROXIES[0], PROXIES[1]])
        for _ in range(DEAD_THRESHOLD):
            pm.mark_dead(PROXIES[0])
        pm.mark_alive(PROXIES[0])
        returned = {pm.get_proxy() for _ in range(6)}
        assert PROXIES[0] in returned

    def test_mark_alive_resets_failure_count(self):
        pm = ProxyManager([PROXIES[0]])
        for _ in range(DEAD_THRESHOLD - 1):
            pm.mark_dead(PROXIES[0])
        pm.mark_alive(PROXIES[0])
        # After reset, needs DEAD_THRESHOLD new failures to die again
        for _ in range(DEAD_THRESHOLD - 1):
            pm.mark_dead(PROXIES[0])
        assert pm.dead_count == 0


class TestLiveCount:
    def test_all_live_initially(self):
        pm = ProxyManager(PROXIES)
        assert pm.live_count == len(PROXIES)

    def test_live_count_decreases_on_dead(self):
        pm = ProxyManager(PROXIES)
        for _ in range(DEAD_THRESHOLD):
            pm.mark_dead(PROXIES[0])
        assert pm.live_count == len(PROXIES) - 1


class TestMaskProxy:
    def test_masks_password(self):
        result = _mask_proxy("http://user:secret@proxy:8080")
        assert "secret" not in result
        assert "***" in result

    def test_no_password_unchanged(self):
        result = _mask_proxy("http://proxy:8080")
        assert result == "http://proxy:8080"
