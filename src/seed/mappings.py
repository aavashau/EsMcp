HEALTHCARE_MAPPING = {
    "mappings": {
        "properties": {
            "_docType": {"type": "keyword"},  # "elig" | "claims"

            # --- elig (patient) fields ---
            "patient_id": {"type": "keyword"},
            "ssn": {"type": "keyword"},
            "first_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "last_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "dob": {"type": "date"},
            "address": {
                "properties": {
                    "street": {"type": "text"},
                    "city": {"type": "keyword"},
                    "state": {"type": "keyword"},
                    "zip": {"type": "keyword"},
                }
            },
            "phone": {"type": "keyword"},
            "email": {"type": "keyword"},
            "insurance_id": {"type": "keyword"},

            # --- claims (encounter) fields ---
            "encounter_id": {"type": "keyword"},
            "provider_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "encounter_date": {"type": "date"},
            "diagnoses": {
                "type": "nested",
                "properties": {
                    "code": {"type": "keyword"},
                    "description": {"type": "text"},
                }
            },
            "note": {"type": "text", "analyzer": "english"},
        }
    }
}
