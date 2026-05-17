import random
from datetime import date, timedelta

from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

ICD10_CODES = [
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("I10", "Essential (primary) hypertension"),
    ("J45.20", "Mild intermittent asthma, uncomplicated"),
    ("M79.3", "Panniculitis"),
    ("F32.1", "Major depressive disorder, single episode, moderate"),
    ("K21.0", "Gastro-esophageal reflux disease with esophagitis"),
    ("N18.3", "Chronic kidney disease, stage 3"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("M54.5", "Low back pain"),
    ("R05.9", "Cough, unspecified"),
]

NOTE_TEMPLATES = [
    "Patient {full_name}, DOB {dob}, presented with {complaint}. Vitals stable. Phone on file: {phone}.",
    "SUBJECTIVE: {full_name} is a {age}-year-old with {diagnosis}. DOB: {dob}. Discussed treatment options.",
    "Follow-up visit for {full_name} (DOB {dob}). Patient reports improvement. Contact: {email}.",
    "Chief complaint: {complaint}. Patient {full_name}, born {dob}, referred by Dr. {provider}.",
    "{full_name} seen today for {diagnosis} management. Date of birth: {dob}. Phone: {phone}.",
    "Assessment: {diagnosis}. Patient {full_name}, DOB {dob}. Plan discussed and agreed upon.",
    "Patient {full_name} (DOB: {dob}) called to report {complaint}. Callback number: {phone}.",
    "Encounter note for {full_name}. Age {age}. Diagnosis: {diagnosis}. Follow-up in 3 months.",
    "{full_name}, born {dob}, presents for annual physical. Email: {email}. All vitals within normal range.",
    "Re: {full_name} (DOB {dob}) — {complaint}. Prescribed treatment per Dr. {provider}.",
]

COMPLAINTS = [
    "chest pain", "shortness of breath", "fatigue", "headache",
    "joint pain", "fever", "abdominal pain", "dizziness",
    "back pain", "nausea", "insomnia", "anxiety",
]


def _random_dob() -> date:
    start = date(1940, 1, 1)
    end = date(2005, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))


def generate_elig(n: int = 500) -> list[dict]:
    records = []
    for i in range(n):
        first = fake.first_name()
        last = fake.last_name()
        dob = _random_dob()
        records.append({
            "_docType": "elig",
            "patient_id": f"MRN-{i:06d}",
            "ssn": fake.ssn(),
            "first_name": first,
            "last_name": last,
            "dob": dob.isoformat(),
            "address": {
                "street": fake.street_address(),
                "city": fake.city(),
                "state": fake.state_abbr(),
                "zip": fake.zipcode(),
            },
            "phone": fake.phone_number(),
            "email": fake.email(),
            "insurance_id": f"INS-{fake.bothify('??####??').upper()}",
        })
    return records


def generate_claims(elig_records: list[dict]) -> list[dict]:
    claims = []
    enc_idx = 0
    for patient in elig_records:
        full_name = f"{patient['first_name']} {patient['last_name']}"
        dob = patient["dob"]
        age = (date.today() - date.fromisoformat(dob)).days // 365
        n_encounters = random.randint(1, 5)

        for _ in range(n_encounters):
            provider = fake.name()
            diagnosis = random.choice(ICD10_CODES)
            complaint = random.choice(COMPLAINTS)
            template = random.choice(NOTE_TEMPLATES)
            note = template.format(
                full_name=full_name,
                dob=dob,
                age=age,
                complaint=complaint,
                diagnosis=diagnosis[1],
                phone=patient["phone"],
                email=patient["email"],
                provider=provider,
            )
            encounter_date = date(
                random.randint(2020, 2024),
                random.randint(1, 12),
                random.randint(1, 28),
            )
            claims.append({
                "_docType": "claims",
                "encounter_id": f"ENC-{enc_idx:06d}",
                "patient_id": patient["patient_id"],
                "provider_name": provider,
                "encounter_date": encounter_date.isoformat(),
                "diagnoses": [{"code": diagnosis[0], "description": diagnosis[1]}],
                "note": note,
            })
            enc_idx += 1
    return claims
