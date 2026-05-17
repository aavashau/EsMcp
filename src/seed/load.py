import sys
import time

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from .generate import generate_claims, generate_elig
from .mappings import HEALTHCARE_MAPPING

INDEX = "healthcare"


def main() -> None:
    es = Elasticsearch("http://localhost:9200")

    for attempt in range(30):
        if es.ping():
            break
        print(f"Waiting for Elasticsearch... ({attempt + 1}/30)")
        time.sleep(2)
    else:
        print("ERROR: Elasticsearch not reachable at http://localhost:9200", file=sys.stderr)
        sys.exit(1)

    if es.indices.exists(index=INDEX):
        es.indices.delete(index=INDEX)
        print(f"Dropped existing index: {INDEX}")
    es.indices.create(index=INDEX, mappings=HEALTHCARE_MAPPING["mappings"])
    print(f"Created index: {INDEX}")

    print("Generating synthetic healthcare data (seed=42)...")
    elig_records = generate_elig(500)
    claims_records = generate_claims(elig_records)
    print(f"Generated {len(elig_records)} elig records, {len(claims_records)} claims records")

    def _actions():
        for rec in elig_records:
            yield {"_index": INDEX, "_id": rec["patient_id"], "_source": rec}
        for rec in claims_records:
            yield {"_index": INDEX, "_id": rec["encounter_id"], "_source": rec}

    ok, _ = bulk(es, _actions(), chunk_size=500)
    print(f"Indexed {ok} total documents ({len(elig_records)} elig + {len(claims_records)} claims)")
    print("Done!")


if __name__ == "__main__":
    main()
