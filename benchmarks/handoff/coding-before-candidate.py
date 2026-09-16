class Deduplicator:
    def __init__(self, ttl):
        self.ttl = ttl
        self.seen = {}

    def accept(self, tenant, event_id, now):
        key = (tenant, event_id)
        seen_at = self.seen.get(key)
        # still fresh if now is inside [seen_at, seen_at+ttl); rollback keeps now < seen_at
        if seen_at is not None and now < seen_at + self.ttl:
            return False
        self.seen[key] = now
        return True


if __name__ == "__main__":
    d = Deduplicator(ttl=10)
    assert d.accept("a", "e1", 100) is True
    assert d.accept("a", "e1", 100) is False  # same instant
    assert d.accept("b", "e1", 100) is True  # tenant isolation
    assert d.accept("a", "e1", 109) is False  # still inside ttl
    assert d.accept("a", "e1", 110) is True  # expired
    assert d.accept("a", "e1", 105) is False  # clock rollback
    assert d.accept("a", "e2", 105) is True  # other id still ok
    print("ok")
