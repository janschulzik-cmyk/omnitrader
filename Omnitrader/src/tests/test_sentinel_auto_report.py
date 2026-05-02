"""Test Sentinel auto-reporting: create mock data and run submission cycle."""
import os
import sys
import json
from datetime import datetime, timezone

# Ensure we can find the src package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SENTINEL_AUTO_REPORT_MIN_SEVERITY"] = "HIGH"
os.environ["SMTP_HOST"] = "smtp.gmail.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = "test@test.com"
os.environ["SMTP_PASSWORD"] = "test"
os.environ["SENDER_EMAIL"] = "test@test.com"
os.environ["DATABASE_URL"] = "sqlite:///data/test_omnitrader.db"

from src.sentinel.sanction_reporter import SanctionReporter
from src.utils.db import init_db, HoneypotEvent, OnChainAlert, get_session, Base


def test_auto_reporting():
    # Initialize test database
    os.makedirs("data", exist_ok=True)
    from src.utils.db import init_db
    import src.utils.db as db_mod
    init_db()
    Base.metadata.create_all(db_mod._engine)

    session = get_session()

    # Clean up any previous test data
    session.query(HoneypotEvent).delete()
    session.query(OnChainAlert).delete()
    session.commit()

    # Create mock HoneypotEvent (simulates an attacker hitting a honeypot)
    hp1 = HoneypotEvent(
        ip_address="192.168.1.100",
        user_agent="Mozilla/5.0",
        method="POST",
        path="/admin/secret",
        route="admin_secrets",
        fake_key_used="fake_api_key_12345",
        timestamp=datetime.utcnow(),
    )
    session.add(hp1)
    session.commit()
    print(f"[OK] Created mock HoneypotEvent: id={hp1.id}, fake_key={hp1.fake_key_used}")

    # Create mock high-severity OnChainAlert
    alert = OnChainAlert(
        alert_type="rugpull_detected",
        network="ethereum",
        severity="HIGH",
        target_address="0xdeadbeef1234567890abcdef",
        wallet_addresses=json.dumps(["0xdeadbeef1234567890abcdef"]),
        tx_hashes=json.dumps(["0xabc123def456"]),
        evidence=json.dumps({"project": "RugPullToken", "team": "Anon"}),
        created_at=datetime.utcnow(),
    )
    session.add(alert)
    session.commit()
    print(f"[OK] Created mock OnChainAlert: id={alert.id}, severity={alert.severity}")

    import tempfile
    from src.sentinel.sanction_reporter import SanctionReporter, SanctionReporterConfig
    
    # Create a temp dir for reports (can't write to /var/log in container)
    tmpdir = tempfile.mkdtemp()
    config = SanctionReporterConfig()
    config.report_output_dir = tmpdir
    reporter = SanctionReporter(config)

    # Patch send_report to always use dry_run mode (no real SMTP)
    original_send = reporter.send_report
    def dry_run_send(report, target="ic3", dry_run=True):
        return original_send(report, target=target, dry_run=True)
    reporter.send_report = dry_run_send

    print("\n[*] Running auto-report cycle...")
    summary = reporter.run_auto_report_cycle()

    print("\n[*] Auto-report cycle results:")
    print(f"    Honeypots checked: {summary['honeypot_checked']}")
    print(f"    Honeypots submitted: {summary['honeypot_submitted']}")
    print(f"    OnChain alerts checked: {summary['onchain_checked']}")
    print(f"    OnChain alerts submitted: {summary['onchain_submitted']}")
    print(f"    Errors: {summary['errors']}")

    # Verify results
    assert summary["honeypot_submitted"] >= 1, f"Expected honeypot submission, got {summary['honeypot_submitted']}"
    assert summary["onchain_submitted"] >= 1, f"Expected onchain submission, got {summary['onchain_submitted']}"
    assert summary["errors"] == 0, f"Expected no errors, got {summary['errors']}"

    print("\n[OK] All assertions passed! Auto-reporting works correctly.")

    # Clean up test data
    session.query(HoneypotEvent).delete()
    session.query(OnChainAlert).delete()
    session.commit()
    session.close()
    print("[*] Test data cleaned up.")

    return True


if __name__ == "__main__":
    try:
        test_auto_reporting()
        print("\n=== Phase 3 Test PASSED ===")
        sys.exit(0)
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
