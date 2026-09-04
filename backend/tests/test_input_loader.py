from __future__ import annotations

import sqlite3
from pathlib import Path

from protocol_optimization_agent.input_loader import load_protocol_analysis_inputs


def test_load_protocol_analysis_inputs_reconstructs_from_sqlite(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    sqlite_path = input_dir / "protocol_database.sqlite"
    con = sqlite3.connect(sqlite_path)
    con.executescript(
        """
        create table protocols (
          protocol_id text primary key,
          paper_id text,
          protocol_name text,
          organoid_type text,
          starting_cell_type text,
          culture_format text,
          source_file text,
          source_page text,
          evidence_quote text,
          confidence_level text,
          extra_json text
        );
        create table stages (
          stage_id text primary key,
          protocol_id text,
          stage_index integer,
          stage_name text,
          day_start real,
          day_end real,
          duration_days real,
          input_cell_state text,
          output_cell_state text,
          medium_base text,
          culture_format text,
          coating_or_matrix text,
          key_operations text,
          evidence_quote text,
          confidence_level text,
          extra_json text
        );
        create table reagents (
          reagent_instance_id text primary key,
          protocol_id text,
          stage_id text,
          original_name text,
          standardized_name text,
          category text,
          pathway text,
          concentration text,
          unit text,
          timing text,
          day_start real,
          day_end real,
          optional integer,
          evidence_quote text,
          confidence_level text,
          extra_json text
        );
        create table evidence_spans (
          evidence_span_id text primary key,
          protocol_id text,
          source_doc_id text,
          source_file text,
          quote text,
          page text,
          section text,
          source_location text,
          table_id text,
          sheet_name text,
          row_index text,
          column_name text,
          cell_range text,
          char_start integer,
          char_end integer,
          extra_json text
        );
        create table field_evidence (
          field_evidence_id text primary key,
          protocol_id text,
          entity_type text,
          entity_id text,
          field_name text,
          source_doc_id text,
          quote text,
          page text,
          section text,
          source_location text,
          table_id text,
          sheet_name text,
          cell_range text,
          extra_json text
        );
        create table lineage_candidate_edges (
          lineage_edge_id text primary key,
          protocol_id text,
          previous_protocol_id text,
          relation_type text,
          confidence_level text,
          evidence_quote text,
          citation_surface_text text,
          citation_type text,
          reference_number text,
          reference_entry text,
          matched_local_file text,
          match_score real,
          resolver_method text,
          depth integer,
          parent_parse_status text,
          llm_relevance_decision text,
          needs_human_review integer,
          extra_json text
        );
        create table validation_flags (
          flag_id text primary key,
          source text,
          severity text,
          issue_type text,
          "check" text,
          message text,
          protocol_id text,
          stage_id text,
          reagent_instance_id text,
          entity_id text,
          extra_json text
        );
        create table papers (
          paper_id text primary key,
          dataset_id text,
          title text,
          first_author text,
          year text,
          doi text,
          pmid text,
          source_file text,
          source_doc_id text,
          extra_json text
        );
        """
    )
    con.execute(
        "insert into protocols values (?,?,?,?,?,?,?,?,?,?,?)",
        ("p1", "paper1", "Protocol 1", None, "hPSC", "2D", "paper1.pdf", "1", "protocol quote", "high", "{}"),
    )
    con.execute(
        "insert into stages values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "s1",
            "p1",
            1,
            "Stage 1",
            0,
            3,
            3,
            "hPSC",
            "DE",
            "medium",
            "2D",
            "Matrigel",
            "culture cells",
            "stage quote",
            "high",
            "{}",
        ),
    )
    con.execute(
        "insert into reagents values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "r1",
            "p1",
            "s1",
            "Activin A",
            "Activin A",
            "growth factor",
            "TGF-beta",
            "100",
            "ng/ml",
            "day 0-3",
            0,
            3,
            0,
            "Activin quote",
            "high",
            "{}",
        ),
    )
    con.execute(
        "insert into papers values (?,?,?,?,?,?,?,?,?,?)",
        ("paper1", "d1", "Paper 1", "A", "2026", "", "", "paper1.pdf", "source1", "{}"),
    )
    con.commit()
    con.close()

    (input_dir / "protocol_factor_events.csv").write_text(
        "protocol_id,paper_id,dataset_id,stage_id,stage_index,day_start,day_end,duration_days,timing,operation,culture_format,cell_state_in,cell_state_out,confidence,evidence_quote,validation_status,factor_type,factor_name,standardized_name,concentration_value,concentration_parse_status,parse_status\n"
        "p1,paper1,d1,s1,1,0,3,3,day 0-3,culture,2D,hPSC,DE,high,Activin quote,evidence_supported,reagent,Activin A,Activin A,100,single_value,single_value\n",
        encoding="utf-8",
    )
    (input_dir / "protocol_evidence_coverage.csv").write_text(
        "protocol_id,stage_quote_coverage,reagent_quote_coverage\np1,1.0,1.0\n",
        encoding="utf-8",
    )
    (input_dir / "protocol_domain_validation_flags.csv").write_text(
        "entity_type,entity_id,paper_or_protocol_id,severity,issue_type,message\n",
        encoding="utf-8",
    )

    loaded = load_protocol_analysis_inputs(input_dir)

    assert loaded["protocol_record_source"] == "protocol_database.sqlite"
    assert loaded["protocol_records"][0]["comparison_ready"] is True
    assert loaded["protocol_records"][0]["stages"][0]["stage_id"] == "s1"
    assert loaded["source_identity_rows"][0]["canonical_paper_id"] == "paper1"
