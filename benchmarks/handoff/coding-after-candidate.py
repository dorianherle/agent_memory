class Deduplicator:
    def __init__(self, ttl):
        self.ttl = ttl
        self.seen = {}
        self._watermark = float("-inf")

    def accept(self, tenant, event_id, now):
        now = max(now, self._watermark)
        self._watermark = now
        self.seen = {k: t for k, t in self.seen.items() if now - t < self.ttl}
        if not event_id:
            return True
        key = (tenant, event_id)
        if key in self.seen:
            return False
        self.seen[key] = now
        return True


if __name__ == "__main__":
    d = Deduplicator(ttl=10)
    assert d.accept("a", "e1", 0) is True
    assert d.accept("a", "e1", 1) is False
    assert d.accept("b", "e1", 1) is True
    assert d.accept("a", "e1", 9) is False
    assert d.accept("a", "e1", 10) is True
    assert d.accept("a", "", 11) is True
    assert d.accept("a", "", 11) is True
    assert ("a", "") not in d.seen
    d.accept("a", "keep", 11)
    d.accept("a", "old", 11)
    d.seen[("a", "old")] = 0
    assert d.accept("a", "", 12) is True
    assert ("a", "old") not in d.seen
    assert ("a", "keep") in d.seen
    first = d.seen[("a", "keep")]
    assert d.accept("a", "keep", 5) is False
    assert d.seen[("a", "keep")] == first
    print("ok")
