#!/usr/bin/env python3
"""Test script for Phase 4: Sleuth Draft Reports workflow."""

import os
import sys
import json
import requests
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

os.environ.setdefault("DATABASE_URL", "sqlite:///omnitrader.db")

from src.utils.db import OnChainAlert, get_session, Base, init_db
from src.sleuth.bounty_reporter import BountyReporter

# Initialize database
init_db()

def create_mock_alert():
    """Create a mock on-chain alert in the database."""
    session = get_session()
    try:
        alert_data = {
            "alert_type": "rugpull_detected",
            "network": "ethereum",
            "severity": "CRITICAL",
            "target_address": "0x1234567890abcdef1234567890abcdef12345678",
            "wallet_addresses": json.dumps([
                "0xabcdef1234567890abcdef1234567890abcdef12",
                "0xfedcba0987654321fedcba0987654321fedcba09"
            ]),
            "tx_hashes": json.dumps([
                "0xabc123def456abc123def456abc123def456abc123def456abc123def456abc1"
            ]),
            "evidence": json.dumps({
                "summary": "Liquidity pool drained by contract owner",
                "timestamp": "2024-01-01T00:00:00Z",
                "description": "Contract owner removed all liquidity"
            }),
            "value_usd": 50000.0,
        }
        
        alert = OnChainAlert(**alert_data)
        session.add(alert)
        session.commit()
        print(f"✓ Created mock alert: #{alert.id} ({alert.alert_type})")
        return alert
    except Exception as e:
        session.rollback()
        print(f"✗ Failed to create alert: {e}")
        raise
    finally:
        session.close()

def test_generate_draft_report(alert_dict):
    """Test generating a draft report from an alert."""
    try:
        reporter = BountyReporter.load()
        result = reporter.generate_draft_report(
            alert=alert_dict,
            target_name="cftc"
        )
        
        if result:
            print(f"✓ Draft report generated:")
            print(f"  - Report ID: {result['report_id']}")
            print(f"  - Submission ID: {result['id']}")
            print(f"  - Target: {result['target']}")
            print(f"  - Status: {result['status']}")
            print(f"  - PDF Path: {result['pdf_path']}")
            
            # Verify PDF file exists
            pdf_path = Path(result['pdf_path'])
            if pdf_path.exists():
                print(f"✓ PDF file exists: {pdf_path} ({pdf_path.stat().st_size} bytes)")
            else:
                print(f"✗ PDF file not found: {pdf_path}")
                return False
            
            return True
        else:
            print("✗ generate_draft_report returned None")
            return False
    except Exception as e:
        print(f"✗ Failed to generate draft report: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_get_draft_reports():
    """Test listing draft reports."""
    try:
        reporter = BountyReporter.load()
        drafts = reporter.get_draft_reports()
        print(f"✓ Found {len(drafts)} draft reports")
        for draft in drafts:
            print(f"  - ID: {draft['id']}, Status: {draft['status']}, Target: {draft.get('submitted_to', '?')}")
        return True
    except Exception as e:
        print(f"✗ Failed to get draft reports: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_approve_report(report_id):
    """Test approving a draft report."""
    try:
        reporter = BountyReporter.load()
        result = reporter.approve_report(report_id)
        
        if result:
            print(f"✓ Report {report_id} approved")
            print(f"  - Status: {result['status']}")
            return True
        else:
            print(f"✗ Failed to approve report {report_id}")
            return False
    except Exception as e:
        print(f"✗ Failed to approve report: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_api_endpoints():
    """Test the API endpoints for draft reports."""
    api_key = os.getenv("OMNITRADER_API_KEY", "test-key")
    base_url = "http://localhost:8000/api/v1"
    
    headers = {"X-API-Key": api_key}
    
     # Test POST /sleuth/generate-draft
    print("\n--- Testing API: POST /sleuth/generate-draft ---")
    try:
        response = requests.post(
            f"{base_url}/sleuth/generate-draft",
            json={
                "alert_id": 1,
                "target": "cftc",
                "report_type": "violation"
            },
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Draft generation API returned: {data['status']}")
            print(f"  - Report ID: {data.get('report_id')}")
            print(f"  - PDF Path: {data.get('pdf_path')}")
        else:
            print(f"✗ API error: {response.status_code} - {response.text}")
            return False
    except requests.exceptions.ConnectionError:
        print("⚠ API server not running (start with: uvicorn src.main:app)")
        return False
    except Exception as e:
        print(f"✗ API error: {e}")
        return False
    
    # Test GET /sleuth/draft-reports
    print("\n--- Testing API: GET /sleuth/draft-reports ---")
    try:
        response = requests.get(f"{base_url}/sleuth/draft-reports", headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Draft reports API returned {data['count']} reports")
        else:
            print(f"✗ API error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ API error: {e}")
        return False
    
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("Phase 4: Sleuth Draft Reports - Manual Test")
    print("=" * 60)
    
    # Step 1: Create mock alert
    print("\n1. Creating mock alert...")
    alert = create_mock_alert()
    
    # Step 2: Generate draft report
    print("\n2. Generating draft report...")
    report_ok = test_generate_draft_report(alert.to_dict())
    
    # Step 3: List draft reports
    print("\n3. Listing draft reports...")
    list_ok = test_get_draft_reports()
    
    # Step 4: Approve a report
    if report_ok and list_ok:
        reporter = BountyReporter.load()
        drafts = reporter.get_draft_reports()
        if drafts:
            print("\n4. Approving first draft report...")
            approve_ok = test_approve_report(drafts[0]['id'])
        else:
            approve_ok = False
    else:
        approve_ok = False
    
    # Step 5: Test API endpoints
    print("\n5. Testing API endpoints...")
    api_ok = test_api_endpoints()
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary:")
    print(f"  - Generate Draft: {'✓' if report_ok else '✗'}")
    print(f"  - List Drafts: {'✓' if list_ok else '✗'}")
    print(f"  - Approve Report: {'✓' if approve_ok else '✗'}")
    print(f"  - API Endpoints: {'✓' if api_ok else '✗'}")
    print("=" * 60)
    
    if all([report_ok, list_ok, approve_ok, api_ok]):
        print("\n✓ All Phase 4 tests PASSED!")
        return 0
    else:
        print("\n✗ Some tests failed. Check logs above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
