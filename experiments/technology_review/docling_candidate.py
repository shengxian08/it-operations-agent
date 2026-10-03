"""Isolated, synthetic-only Docling CPU experiment; never a business adapter.

Prepare downloads only public Heron/Torch and TableFormer/accurate files. Run is
intended for a Docker --network none container with the models/input read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time
import traceback


NAMES = (
    "ruled-table", "prose", "merged", "borderless",
    "mixed-supported-and-borderless", "scan-with-footer", "tiled-scan",
)
HEADERS = ["服务等级", "响应时间（分钟）", "适用范围"]
ROWS = [["普通", "240", "一般问题"], ["紧急", "15", "业务中断"]]
CONDITION = "适用条件：工作日。"
FOOTNOTE = "注：响应时间不等于解决时间。"


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def versions() -> dict:
    packages = ("docling", "docling-core", "docling-ibm-models", "docling-parse", "torch", "torchvision", "transformers", "huggingface-hub", "pymupdf", "numpy", "pydantic")
    result = {"python": sys.version, "platform": platform.platform()}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    result["all_distributions"] = sorted(
        ({"name": d.metadata.get("Name"), "version": d.version} for d in importlib.metadata.distributions()),
        key=lambda d: (d["name"] or "").lower(),
    )
    return result


def prepare(models: Path, output: Path) -> None:
    from huggingface_hub import HfApi, snapshot_download
    from docling.datamodel.layout_model_specs import DOCLING_LAYOUT_HERON

    models.mkdir(parents=True, exist_ok=True)
    # Resolve mutable branch/tag to immutable commits once, use anonymous HTTP.
    requests = [
        (DOCLING_LAYOUT_HERON.repo_id, DOCLING_LAYOUT_HERON.revision,
         ["config.json", "preprocessor_config.json", "model.safetensors", "README.md", "LICENSE*"],
         "docling-project--docling-layout-heron"),
        ("docling-project/docling-models", "v2.3.0",
         ["model_artifacts/tableformer/accurate/*", "README.md", "LICENSE*"],
         "docling-project--docling-models"),
    ]
    manifest = {"versions": versions(), "models": [], "downloaded_only_public_assets": True}
    dump(output / "prepare-progress.json", manifest)
    api = HfApi(token=False)
    for repo, revision, patterns, folder in requests:
        info = api.model_info(repo, revision=revision, token=False)
        print(f"Downloading {repo}@{info.sha} patterns={patterns}", flush=True)
        start = time.perf_counter()
        snapshot_download(repo_id=repo, revision=info.sha, local_dir=models / folder,
                          allow_patterns=patterns, token=False, max_workers=2)
        files = [{"path": str(p.relative_to(models)), "bytes": p.stat().st_size, "sha256": sha256(p)}
                 for p in sorted((models / folder).rglob("*"))
                 if p.is_file() and ".cache" not in p.parts]
        manifest["models"].append({"repo": repo, "requested_revision": revision, "resolved_commit": info.sha,
                                   "allow_patterns": patterns, "seconds": time.perf_counter() - start, "files": files})
        dump(output / "prepare-progress.json", manifest)
    dump(output / "model-manifest.json", manifest)


def rss() -> dict:
    current = None
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                current = int(line.split()[1]) * 1024
    except OSError:
        pass
    return {"current_rss_bytes": current, "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}


def cgroup() -> dict:
    values = {}
    for name in ("cpu.max", "memory.max", "memory.peak", "memory.events"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            values[name] = path.read_text().strip()
    return values


def known_grid_containment(cells: list[dict]) -> dict:
    """Compare actual boxes to the fixed synthetic grid; never create boxes."""
    x, y = [45, 175, 340, 550], [110, 137, 164, 191]
    checks = []
    for cell in cells:
        row, col = cell.get("start_row_offset_idx"), cell.get("start_col_offset_idx")
        box = cell.get("bbox")
        valid = isinstance(row, int) and isinstance(col, int) and 0 <= row < 3 and 0 <= col < 3 and box is not None
        normalized = None
        if valid:
            top, bottom = min(box["t"], box["b"]), max(box["t"], box["b"])
            if box.get("coord_origin") == "BOTTOMLEFT":
                top, bottom = 842 - bottom, 842 - top
            elif box.get("coord_origin") != "TOPLEFT":
                valid = False
            normalized = [min(box["l"], box["r"]), top, max(box["l"], box["r"]), bottom]
            valid = valid and normalized[0] >= x[col] - 2 and normalized[2] <= x[col + 1] + 2 and normalized[1] >= y[row] - 2 and normalized[3] <= y[row + 1] + 2 and normalized[0] < normalized[2] and top < bottom
        checks.append({"text": cell.get("text"), "row": row, "col": col, "actual_bbox": box,
                       "actual_bbox_as_top_left": normalized, "inside_known_synthetic_cell_with_2pt_tolerance": valid})
    return {"source_grid_x": x, "source_grid_y": y, "source_page_height": 842, "tolerance_points": 2,
            "cell_checks": checks, "all_cells_inside": len(checks) == 9 and all(c["inside_known_synthetic_cell_with_2pt_tolerance"] for c in checks)}


def evidence(document: dict, name: str, status: str) -> dict:
    tables = document.get("tables", [])
    texts = document.get("texts", [])
    all_text = "\n".join(str(t.get("text", "")) for t in texts)
    cells = [c for table in tables for c in table.get("data", {}).get("table_cells", [])]
    cell_texts = [str(c.get("text", "")) for c in cells]
    actual = {
        "api_status": status,
        "pages": document.get("pages"),
        "tables": tables,  # exact serialized cells/spans/header flags/bboxes/provenance
        "texts": texts,    # exact paragraph/footnote labels/text/provenance
        "cell_count": len(cells),
        "cells_with_bbox": sum(c.get("bbox") is not None for c in cells),
        "cell_texts": cell_texts,
        "header_flagged_texts": [c.get("text") for c in cells if c.get("column_header")],
        "unit_literal_present": "分钟" in "\n".join(cell_texts),
        "condition_literal_present": CONDITION in all_text,
        "footnote_literal_present": FOOTNOTE in all_text,
        "within_baseline_supported_contract": name in ("ruled-table", "prose"),
    }
    success = status.lower().endswith("success") and "partial" not in status.lower()
    if name not in ("ruled-table", "prose"):
        actual["boundary_disposition"] = "api_success_outside_baseline_contract_requires_review" if success else "candidate_failed_or_rejected"
        actual["automatic_business_acceptance"] = False
        actual["quality_pass"] = None
        if name in ("scan-with-footer", "tiled-scan"):
            actual["scan_image_text_recovered"] = any(v in all_text for v in ("15分钟", "Critical response"))
            actual["risk"] = "API success with footer alone is incomplete scanned-document extraction; OCR disabled by design."
    elif name == "ruled-table":
        expected_cells = HEADERS + [v for row in ROWS for v in row]
        offsets = {(c.get("start_row_offset_idx"), c.get("start_col_offset_idx")): c.get("text") for c in cells}
        expected_offsets = {(r, col): text for r, row in enumerate([HEADERS, *ROWS]) for col, text in enumerate(row)}
        checks = {
            "exactly_one_3x3_table": len(tables) == 1 and tables[0].get("data", {}).get("num_rows") == 3 and tables[0].get("data", {}).get("num_cols") == 3,
            "nine_exact_cell_texts": sorted(cell_texts) == sorted(expected_cells),
            "row_col_relationships_exact": offsets == expected_offsets,
            "exact_column_headers": actual["header_flagged_texts"] == HEADERS,
            "all_real_cell_bboxes_present": len(cells) == 9 and actual["cells_with_bbox"] == 9,
            "table_page_and_bbox": len(tables) == 1 and bool(tables[0].get("prov")) and all(p.get("page_no") == 1 and p.get("bbox") for p in tables[0].get("prov", [])),
            "condition_preserved": actual["condition_literal_present"],
            "footnote_preserved": actual["footnote_literal_present"],
        }
        actual["known_source_geometry_comparison"] = known_grid_containment(cells)
        checks["bbox_inside_corresponding_source_cell"] = actual["known_source_geometry_comparison"]["all_cells_inside"]
        actual["synthetic_structure_checks"] = checks
        actual["quality_pass"] = success and all(checks.values())
        actual["boundary_disposition"] = "synthetic_checks_pass_manual_bbox_review_pending" if actual["quality_pass"] else "synthetic_checks_failed"
        actual["automatic_business_acceptance"] = False
    else:
        lines = [f"The team {verb} each request before a change is approved." for verb in ("reviews", "verifies", "records", "closes", "audits")]
        actual["synthetic_structure_checks"] = {"all_five_sentences": all(t in all_text for t in lines), "no_false_table": not tables,
                                                "all_text_items_have_page_bbox": all(t.get("prov") and all(p.get("page_no") == 1 and p.get("bbox") for p in t["prov"]) for t in texts)}
        actual["quality_pass"] = success and all(actual["synthetic_structure_checks"].values())
        actual["boundary_disposition"] = "synthetic_checks_pass_manual_bbox_review_pending" if actual["quality_pass"] else "synthetic_checks_failed"
        actual["automatic_business_acceptance"] = False
    return actual


def run(models: Path, inputs: Path, output: Path, model_manifest: Path) -> None:
    import torch
    import pymupdf
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, LayoutObjectDetectionOptions, TableStructureOptions, TableFormerMode
    from docling.datamodel.object_detection_engine_options import TransformersObjectDetectionEngineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    assert importlib.metadata.version("docling") == "2.132.0"
    locked_models = json.loads(model_manifest.read_text(encoding="utf-8"))
    for model in locked_models["models"]:
        for artifact in model["files"]:
            assert sha256(models / artifact["path"]) == artifact["sha256"], f"model hash mismatch: {artifact['path']}"
    dump(output / "verified-model-manifest.json", locked_models)
    torch.set_num_threads(2)
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True,
        do_picture_description=False, do_picture_classification=False,
        do_code_enrichment=False, do_formula_enrichment=False,
        enable_remote_services=False, allow_external_plugins=False,
        artifacts_path=models, document_timeout=120,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
        layout_options=LayoutObjectDetectionOptions(engine_options=TransformersObjectDetectionEngineOptions(compile_model=False)),
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True))
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_cls=StandardPdfPipeline, pipeline_options=opts)})
    summary = {"versions": versions(), "device": "cpu", "threads": 2, "network_required_during_run": False,
               "pipeline_class": "StandardPdfPipeline", "pipeline_options": opts.model_dump(mode="json"),
               "sample_origin": "synthetic; not enterprise failure samples", "business_code_migrated": False,
               "resource_limits": cgroup(),
               "timing_definition": "convert() wall time only, excluding process imports/rendering/JSON serialization; first document is cold pipeline initialization; each sample setup pass precedes three warm runs of the same converter", "samples": []}
    dump(output / "environment.json", summary)
    for name in NAMES:
        path = inputs / f"{name}.pdf"
        sample = {"name": name, "input_sha256": sha256(path),
                  "baseline_contract_expected": "accepted" if name in ("ruled-table", "prose") else "rejected", "runs": []}
        with pymupdf.open(path) as original:
            for page in original:
                dest = output / name / f"source-page-{page.number + 1}.png"
                dest.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(dest)
        print(f"Converting {name}", flush=True)
        for repeat in range(4):
            started = time.perf_counter()
            try:
                result = converter.convert(path, raises_on_error=False)
                elapsed = time.perf_counter() - started
                status = str(result.status.value)
                document = result.document.model_dump(mode="json")
                dump(output / name / f"document-{repeat}.json", document)
                actual = evidence(document, name, status)
                dump(output / name / f"evidence-{repeat}.json", actual)
                record = {"repeat": repeat, "phase": "setup" if repeat == 0 else "warm", "seconds": elapsed,
                          "status": status, "errors": [e.model_dump(mode="json") for e in result.errors],
                          "quality_pass": actual["quality_pass"], "boundary_disposition": actual["boundary_disposition"], **rss()}
            except Exception as exc:
                record = {"repeat": repeat, "phase": "setup" if repeat == 0 else "warm", "seconds": time.perf_counter() - started,
                          "status": "exception", "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(), **rss()}
                sample["runs"].append(record)
                dump(output / name / "failure.json", record)
                # Do not repeat a dependency/model/environment failure to make it pass.
                break
            sample["runs"].append(record)
            dump(output / name / "runs.json", sample)
            print(json.dumps({"name": name, **record}, ensure_ascii=False), flush=True)
        summary["samples"].append(sample)
        dump(output / "results.json", summary)
        if sample["runs"][-1]["status"] == "exception":
            raise RuntimeError(f"Stopped at first environment/model exception in {name}; see failure.json")
    summary["final_rss"] = rss()
    summary["final_cgroup"] = cgroup()
    dump(output / "results.json", summary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--model-manifest", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == "prepare":
            prepare(args.models, args.output)
        else:
            assert args.inputs is not None
            assert args.model_manifest is not None
            run(args.models, args.inputs, args.output, args.model_manifest)
    except Exception as exc:
        dump(args.output / f"{args.mode}-error.json", {"error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(), "versions": versions()})
        raise


if __name__ == "__main__":
    main()
