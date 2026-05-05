"""Trigger Sleuth scanner to generate a draft bounty report from a mock alert."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from src.sleuth.onchain_scanner import OnChainScanner
from src.sleuth.bounty_reporter import BountyReporter
from src.utils.db import OnChainAlert, get_session

def main():
    # Create mock alert data
    mock_alert = {
        "alert_type": "SUSPICIOUS_CONCENTRATION",
        "severity": "HIGH",
        "network": "ethereum",
        "wallet_addresses": [
            "0x1234567890abcdef1234567890abcdef12345678",
            "0xabcdef1234567890abcdef1234567890abcdef12"
        ],
        "tx_hashes": [
            "0xabc123def456abc123def456abc123def456abc123def456abc123def456abc1",
            "0xdef456abc123def456abc123def456abc123def456abc123def456abc123def4"
        ],
        "summary": "Large token concentration detected in single wallet cluster. "
                   "Potential market manipulation via coordinated buying.",
        "evidence": {
            "cluster_size": 5,
            "total_value_usd": 2500000,
            "coordinated_transfers": 12,
            "time_window": "2026-05-01 to 2026-05-04"
        },
        "target_address": "0x1234567890abcdef1234567890abcdef12345678",
        "value_usd": 2500000.00
    }

    # Save alert to DB
    session = get_session()
    try:
        alert_record = OnChainAlert(
            alert_type=mock_alert["alert_type"],
            network=mock_alert["network"],
            severity=mock_alert["severity"],
            wallet_addresses=json.dumps(mock_alert["wallet_addresses"]),
            tx_hashes=json.dumps(mock_alert["tx_hashes"]),
            evidence=json.dumps(mock_alert.get("evidence", {})),
            target_address=mock_alert["target_address"],
            value_usd=mock_alert["value_usd"],
            created_at=datetime.utcnow(),
        )
        session.add(alert_record)
        session.commit()
        alert_id = alert_record.id
        print(f"✓ Mock alert inserted into DB (id={alert_id})")
    finally:
        session.close()

    # Generate draft report
    reporter = BountyReporter.load()
    result = reporter.generate_draft_report(mock_alert, target_name="cftc")

    if result:
        print(f"\n✓ Draft report generated:")
        print(f"  Report ID: {result['report_id']}")
        print(f"  PDF Path: {result['pdf_path']}")
        print(f"  Target: {result['target_name']}")
        print(f"  Status: {result['status']}")

        # Verify PDF file exists
        if os.path.exists(result['pdf_path']):
            size = os.path.getsize(result['pdf_path'])
            print(f"  PDF Size: {size} bytes ✓")
        else:
            print(f"  ERROR: PDF file not found at {result['pdf_path']}")
    else:
        print("✗ Failed to generate draft report")

    # List all draft reports
    drafts = reporter.get_draft_reports()
    print(f"\n📋 Draft Reports in DB ({len(drafts)}):")
    for d in drafts:
        print(f"  ID: {d['id']} | Status: {d.get('status', '?')} | "
              f"Event: {d.get('event_type', '?')}")

if __name__ == "__main__":
    main()