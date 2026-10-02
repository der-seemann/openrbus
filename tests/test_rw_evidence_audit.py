"""The source audit classifies evidence without granting write permission."""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from tools.audit_rw_evidence import (
    _category,
    _iae_inventory,
    _rxdx_cache_evidence,
    _rxdx_category,
    _rxdx_inventory,
)


def _register(*, writable: bool = True) -> dict[str, object]:
    return {"access": {"write": writable}}


class RegisterEvidenceCategoryTests(unittest.TestCase):
    def test_declared_read_only_remains_read_only_with_iae_write_metadata(self) -> None:
        self.assertEqual(
            _category(_register(writable=False), [{"writable": 1}]), "declared_read_only"
        )

    def test_global_write_declaration_without_iae_is_not_a_write_confirmation(self) -> None:
        self.assertEqual(_category(_register(), []), "write_declared_iae_evidence_missing")

    def test_iae_positive_is_metadata_only_and_needs_separate_safety_gate(self) -> None:
        self.assertEqual(
            _category(_register(), [{"writable": 1}]),
            "iae_write_metadata_requires_safety_gate",
        )

    def test_iae_cross_source_disagreement_is_kept_as_conflict(self) -> None:
        self.assertEqual(
            _category(_register(), [{"writable": 1}, {"writable": 0}]),
            "iae_writability_conflict",
        )

    def test_iae_negative_overrides_global_declaration_for_safe_projection(self) -> None:
        self.assertEqual(
            _category(_register(), [{"writable": 0}]),
            "iae_read_only_despite_write_declaration",
        )

    def test_iae_missing_write_flag_stays_unknown_even_with_negative_rows(self) -> None:
        self.assertEqual(
            _category(_register(), [{"writable": 0}, {"writable": None}]),
            "iae_writability_unknown",
        )

    def test_rxdx_inventory_deduplicates_bytes_without_exposing_paths_or_hashes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.rxdx").write_bytes(b"primary export")
            (root / "duplicate.rxdx").write_bytes(b"primary export")
            (root / "distinct.rxdx").write_bytes(b"another export")
            with ZipFile(root / "archive.rxdx", "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("member.xml", "<root />")
            (root / "archive-copy.rxdx").write_bytes((root / "archive.rxdx").read_bytes())

            result = _rxdx_inventory(root)

        self.assertEqual(result["files"], 5)
        self.assertEqual(result["unique_contents"], 3)
        self.assertEqual(result["duplicates"], 2)
        self.assertEqual(result["unique_zip_archives"], 1)
        self.assertEqual(result["zip_members"], 1)
        self.assertEqual(result["zip_plain_members"], 1)
        self.assertFalse(result["r_w_metadata_ingested"])
        self.assertNotIn("paths", result)
        self.assertNotIn("hashes", result)
        self.assertEqual(_rxdx_category(result), "rxdx_primary_exports_located_not_parsed")

    def test_unscanned_rxdx_is_not_classified_as_absent(self) -> None:
        result = _rxdx_inventory(None)
        self.assertEqual(_rxdx_category(result), "rxdx_inventory_not_scanned")

    def test_iae_inventory_matches_unique_source_hashes_without_exposing_them(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.iae").write_bytes(b"normalized source")
            (root / "duplicate.iae").write_bytes(b"normalized source")
            expected = hashlib.sha256(b"normalized source").hexdigest()

            result = _iae_inventory(root, {expected})

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["unique_contents"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["matching_unique_contents"], 1)
        self.assertNotIn("hashes", result)
        self.assertNotIn("paths", result)

    def test_extracted_rxdx_cache_metadata_does_not_authorize_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "N-P6-0x00000210-P515-U3.xml").write_text(
                '<root><bdrcan><parameters><sdo index="2001" subindex="02" '
                'type="uint16" length="2" /></parameters></bdrcan></root>',
                encoding="utf-8",
            )

            evidence, inventory = _rxdx_cache_evidence(root)

        row = evidence["2001:02"]
        self.assertEqual(row["device_families"], ["EHC-16"])
        self.assertEqual(row["wire_types"], ["UINT16"])
        self.assertEqual(row["classification"], "presence_and_wire_type_only")
        self.assertEqual(row["writability"], "unassessed")
        self.assertFalse(row["can_promote_write"])
        self.assertEqual(inventory["unique_addresses"], 1)

    def test_configuration_readonly_flag_maps_to_sdo_but_stays_unassessed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            xml = """<root>
                  <configuration><configurations name="parameter">
                    <config readonly="false"><function byte="10" /></config>
                    <config readonly="true"><function byte="11" /></config>
                  </configurations></configuration>
                  <bdrcan><parameters>
                    <sdo index="2001" subindex="02" type="uint16" length="2" byte="10" />
                  </parameters></bdrcan>
                </root>"""
            (root / "N-P6-0x00000210-P515-U3.xml").write_text(xml, encoding="utf-8")
            (root / "duplicate.xml").write_text(xml, encoding="utf-8")

            evidence, inventory = _rxdx_cache_evidence(root)

        row = evidence["2001:02"]
        self.assertEqual(row["configuration_readonly_values"], ["false", "true"])
        self.assertEqual(row["configuration_readonly_status"], "mixed")
        self.assertEqual(row["classification"], "presence_type_and_configuration_readonly_metadata")
        self.assertEqual(row["configuration_readonly_occurrences"], 2)
        self.assertEqual(row["writability"], "unassessed")
        self.assertFalse(row["can_promote_write"])
        self.assertEqual(inventory["configuration_readonly_mapped"], 2)
        self.assertEqual(inventory["configuration_readonly_conflicting_registers"], 1)
        self.assertEqual(inventory["unique_contents"], 1)
        self.assertEqual(inventory["duplicates"], 1)
