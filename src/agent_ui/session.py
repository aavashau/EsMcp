class InMemorySessionStore:
    """In-memory conversation history keyed by session UUID. Swap for Redis in production."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}

    def get(self, session_id: str) -> list[dict]:
        return self._store.setdefault(session_id, [])
