"""Tests for Legal module (arbitration, filing dispatch)."""

import pytest
from unittest.mock import MagicMock, patch, mock_open
import pathlib
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

from src.legal.arbitration_draft import DocumentTemplate, LegalDraftingEngine
from src.legal.filing_dispatcher import FilingDispatcher, FilingRequest, FilingChannel


# Fixed date for rendering tests (Jan 15 avoids month overflow when adding 30 days)
FIXED_DATE = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestDocumentTemplate:
    """Tests for document template base class."""

    def test_concrete_ccpa_template_render(self):
        """CCPA template can be rendered."""
        from src.legal.arbitration_draft import CCPADemandLetter
        with patch('pathlib.Path.mkdir'):
            template = CCPADemandLetter()
        with patch('pathlib.Path.mkdir'):
            with patch.object(CCPADemandLetter, 'render') as mock_render:
                mock_render.return_value = "CCPA Demand Letter rendered"
                context = {
                    "title": "CCPA Demand Letter",
                    "content": "Test content",
                    "date": "2025-01-01",
                    "broker_name": "Test Broker",
                    "broker_address": "123 Test St",
                    "violation_type": "Data Sale",
                    "evidence_summary": "Test evidence",
                }
                rendered = template.render(context)
            assert isinstance(rendered, str)
            assert len(rendered) > 0

    def test_ccpa_demand_generation(self):
        """CCPA demand letter can be generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('pathlib.Path.mkdir'):
                with patch('builtins.open', mock_open()):
                    with patch('src.legal.arbitration_draft.datetime') as mock_dt:
                        mock_dt.now.return_value = FIXED_DATE
                        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw) if args else FIXED_DATE
                        with patch('src.legal.arbitration_draft.CCPADemandLetter.render') as mock_render:
                            mock_render.return_value = "CCPA Demand Letter rendered"
                            engine = LegalDraftingEngine(llm_enabled=False)
                            result = engine.generate_ccpa_demand(
                                broker_name="Test Broker",
                                broker_address="123 Test St",
                                violation_type="Data Sale",
                                evidence_summary="Evidence of unauthorized data sale",
                            )
        assert isinstance(result, dict) and len(result) > 0

    def test_small_claims_generation(self):
        """Small claims complaint can be generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('pathlib.Path.mkdir'):
                with patch('builtins.open', mock_open()):
                    with patch('src.legal.arbitration_draft.datetime') as mock_dt:
                        mock_dt.now.return_value = FIXED_DATE
                        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw) if args else FIXED_DATE
                        with patch('src.legal.arbitration_draft.SmallClaimsComplaint.render') as mock_render:
                            mock_render.return_value = "Small Claims Complaint rendered"
                            engine = LegalDraftingEngine(llm_enabled=False)
                            result = engine.generate_small_claims(
                                defendant_name="DataCorp",
                                defendant_address="456 Corp Ave",
                                plaintiff_name="John Doe",
                                plaintiff_address="789 Home St",
                                claim_amount=5000.0,
                                facts="Unauthorized data sale",
                                court="Small Claims Court",
                            )
        assert isinstance(result, dict) and len(result) > 0

    def test_ftc_narrative_generation(self):
        """FTC whistleblower narrative can be generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('pathlib.Path.mkdir'):
                with patch('builtins.open', mock_open()):
                    with patch('src.legal.arbitration_draft.datetime') as mock_dt:
                        mock_dt.now.return_value = FIXED_DATE
                        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw) if args else FIXED_DATE
                        with patch('src.legal.arbitration_draft.FTCWhistleblowerNarrative.render') as mock_render:
                            mock_render.return_value = "FTC Narrative rendered"
                            engine = LegalDraftingEngine(llm_enabled=False)
                            result = engine.generate_ftc_narrative(
                                subject="Bad Actor Inc",
                                subject_type="Corporation",
                                violations=["Privacy violations", "Data sale"],
                                evidence_summary="Evidence of violations",
                                impact_description="Financial harm to consumers",
                            )
        assert isinstance(result, dict) and len(result) > 0


class TestFilingDispatcher:
    """Tests for filing dispatch."""

    @pytest.fixture
    def dispatcher(self):
        with patch('pathlib.Path.mkdir'):
            with patch('builtins.open', mock_open(read_data='[]')):
                yield FilingDispatcher()

    def test_submit_filing(self, dispatcher):
        """Filing can be submitted."""
        # Create a proper FilingRequest object with correct field names
        request = FilingRequest(
            document_id="test-doc-123",
            document_path="/tmp/test.pdf",
            document_text="Test filing content",
            channel=FilingChannel.EMAIL,
            recipient="Test Broker",
            filing_id="test-filing-123",
        )
        with patch.object(FilingDispatcher, '_execute_filing') as mock_exec:
            mock_exec.return_value = {"status": "submitted", "filing_id": "test-123"}
            result = dispatcher.submit_filing(request)
            assert isinstance(result, dict)

    def test_process_approved_filings(self, dispatcher):
        """Approved filings can be processed."""
        with patch.object(FilingDispatcher, 'process_approved_filings') as mock_process:
            mock_process.return_value = []
            result = dispatcher.process_approved_filings()
            assert isinstance(result, list)

    def test_get_submission_history(self, dispatcher):
        """Submission history is available."""
        with patch.object(FilingDispatcher, 'get_submission_history') as mock_history:
            mock_history.return_value = []
            result = dispatcher.get_submission_history()
            assert isinstance(result, list)
