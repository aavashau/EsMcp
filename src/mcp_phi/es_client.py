from elasticsearch import Elasticsearch
from .config import settings

_client: Elasticsearch | None = None


def get_client() -> Elasticsearch:
    global _client
    if _client is None:
        _client = Elasticsearch(settings.es_url)
    return _client
