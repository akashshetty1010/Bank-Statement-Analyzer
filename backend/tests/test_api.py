from pathlib import Path
from fastapi.testclient import TestClient
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'healthy'


def test_frontend_served():
    r = client.get('/')
    assert r.status_code == 200
    assert 'Statement Analyzer' in r.text


def test_upload_rejects_unsupported_type():
    r = client.post('/api/v1/documents/upload', files={'file': ('x.txt', b'hello', 'text/plain')})
    assert r.status_code == 400


def test_ollama_fallback_is_enabled_by_default(monkeypatch):
    from bsa_processor import classifier
    from bsa_processor.models import Transaction

    called = {"value": False}

    def fake_fallback(txn):
        called["value"] = True
        return "DEBIT", "P2P", 0.88, "LLM"

    monkeypatch.setattr(classifier, "_ollama_fallback", fake_fallback)
    txn = Transaction(
        transaction_id="ollama-default-test",
        date="2025-01-01",
        value_date="2025-01-01",
        narration="UNRECOGNIZED XYZ PAYMENT 12345",
        debit=123.0,
        credit=0.0,
        running_balance=1000.0,
        account_number="123",
        month="2025-01",
        counterparty="UNKNOWN",
        payment_channel="OTHER",
        currency="INR",
        special_attributes={},
        page_number=1,
    )
    class Result:
        transactions = [txn]
        issues = []

    classifier.classify_document(Result())
    assert called["value"] is True
    assert txn.classification_method == "LLM"
    assert txn.classification_level_2 == "P2P"


def test_multi_account_sample_extracts_separate_account_types():
    from bsa_processor.processor import process_statement
    sample = ROOT / 'samples' / 'multi_account_statement.pdf'
    result = process_statement(sample, ROOT / 'data' / 'test_multi_account')
    assert len(result.transactions) == 12
    groups = {(t.account_number, t.account_type) for t in result.transactions}
    assert groups == {
        ('1000012345', 'INDIVIDUAL'),
        ('2000023456', 'JOINT'),
        ('3000034567', 'CREDIT CARD'),
    }


def test_digital_column_detection_keeps_debits_and_credits_correct():
    from bsa_processor.processor import process_statement
    sample = ROOT / 'samples' / 'indian_bank_digital.pdf'
    result = process_statement(sample, ROOT / 'data' / 'test_digital_columns')
    salary = next(t for t in result.transactions if 'SALARY CREDIT' in t.narration)
    emi = next(t for t in result.transactions if 'EMI HOME LOAN' in t.narration)
    assert salary.credit == 85000.0 and salary.debit == 0.0
    assert emi.debit == 18500.0 and emi.credit == 0.0


def test_ollama_model_discovery_is_cached_for_multiple_fallbacks(monkeypatch):
    from bsa_processor import classifier
    from bsa_processor.models import Transaction
    import json

    calls = {"tags": 0, "generate": 0}

    class Resp:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return self.payload

    def fake_urlopen(request, timeout=0):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith('/api/tags'):
            calls['tags'] += 1
            return Resp(json.dumps({"models":[{"name":"test-model"}]}).encode())
        calls['generate'] += 1
        return Resp(json.dumps({"response":json.dumps({"level_1":"DEBIT","category":"OTHER","confidence":0.81})}).encode())

    classifier._ollama_model.cache_clear()
    classifier._ollama_cached_classification.cache_clear()
    monkeypatch.delenv('BSA_OLLAMA_MODEL', raising=False)
    monkeypatch.setattr(classifier.urllib.request, 'urlopen', fake_urlopen)

    def txn(narration):
        return Transaction(transaction_id=narration, date='2025-01-01', value_date='2025-01-01', narration=narration,
            debit=10.0, credit=0.0, running_balance=100.0, account_number='1', month='2025-01',
            counterparty='UNKNOWN', payment_channel='OTHER', currency='INR', special_attributes={}, page_number=1)

    assert classifier._ollama_fallback(txn('UNKNOWN A'))[3] == 'LLM'
    assert classifier._ollama_fallback(txn('UNKNOWN B'))[3] == 'LLM'
    assert calls['tags'] == 1
    assert calls['generate'] == 2
