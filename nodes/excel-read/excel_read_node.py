"""Bounded, streaming workbook extraction. Adapted from mail-extension's Excel reader."""
import base64
import io
import math
import re
import zipfile
from pathlib import Path

from cli_runtime import register_node_class, run_registered_node_from_cli
from ingestion_io import descriptor, digest_file, integer, json_bytes, temporary, memory_budget

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def column_name(number):
    out = ""
    while number:
        number, n = divmod(number - 1, 26)
        out = chr(65 + n) + out
    return out


def coordinate(text):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", text or "")
    if not match:
        raise ValueError("invalid_cell_coordinate")
    col = 0
    for char in match[1]:
        col = col * 26 + ord(char) - 64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        raise ValueError("cell_coordinate_out_of_range")
    return row, col


def _source(file, attachment, archive_entry, max_bytes, owned):
    source = attachment or file
    if not isinstance(source, dict):
        raise ValueError("file_or_attachment_required")
    filename = source.get("filename", "")
    if "tmp_path" in source:
        path = Path(source["tmp_path"])
        limit = 500_000_000 if archive_entry or source.get("archive_entry") else max_bytes
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("input_file_size_limit")
    elif "content_base64" in source:
        encoded = source["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > (max_bytes + 2) // 3 * 4:
            raise ValueError("input_file_size_limit")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > max_bytes:
            raise ValueError("input_file_size_limit")
        path = temporary(".input")
        owned.append(path)
        path.write_bytes(data)
    elif file and (archive_entry or source.get("archive_entry")):
        path = Path(file["tmp_path"])
        filename = source.get("filename", "")
    else:
        raise ValueError("materialized_file_required")
    member = archive_entry or source.get("archive_entry")
    if member:
        bundle = Path(file["tmp_path"]) if file else path
        with zipfile.ZipFile(bundle) as archive:
            if member not in archive.namelist():
                raise ValueError("archive_entry_missing")
            info = archive.getinfo(member)
            if info.file_size > max_bytes:
                raise ValueError("attachment_size_limit")
            path = temporary(".input")
            owned.append(path)
            with archive.open(info) as src, path.open("wb") as dst:
                total = 0
                while chunk := src.read(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("attachment_size_limit")
                    dst.write(chunk)
        filename = source.get("filename") or member
    digest = digest_file(path)
    if source.get("sha256") and source["sha256"].lower() != digest:
        raise ValueError("attachment_hash_mismatch")
    return path, filename, digest, source.get("attachment_id") or digest


class Sink:
    def __init__(self, path, max_bytes, preview_limit):
        self.stream = path.open("wb")
        self.max_bytes, self.preview_limit = max_bytes, preview_limit
        self.size, self.preview_bytes, self.count = 0, 0, 0
        self.preview, self.issues, self.issue_counts = [], [], {}

    def write(self, record):
        raw = json_bytes(record) + b"\n"
        self.size += len(raw)
        if self.size > self.max_bytes:
            raise ValueError("result_file_size_limit")
        self.stream.write(raw)
        if record["record_type"] == "cell":
            self.count += 1
            if len(self.preview) < self.preview_limit and self.preview_bytes + len(raw) <= 65536:
                self.preview.append(record)
                self.preview_bytes += len(raw)

    def issue(self, code, **details):
        self.issue_counts[code] = self.issue_counts.get(code, 0) + 1
        record = {"record_type": "issue", "code": code, **details}
        self.write(record)
        if len(self.issues) < 50:
            self.issues.append(record)


def xml_events(stream):
    from defusedxml.ElementTree import iterparse
    return iterparse(stream, events=("start", "end"), forbid_dtd=True, forbid_entities=True,
                     forbid_external=True)


def xml_root(stream):
    from defusedxml.ElementTree import parse
    return parse(stream, forbid_dtd=True, forbid_entities=True, forbid_external=True).getroot()


def _xlsx(path, sink, max_cells, max_expanded):
    import posixpath
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
            raise ValueError("zip_entry_limit_or_duplicate")
        if sum(i.file_size for i in infos) > max_expanded:
            raise ValueError("workbook_expanded_size_limit")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as stream:
                root = None
                for event, element in xml_events(stream):
                    if root is None:
                        root = element
                    if event == "end" and element.tag == MAIN + "si":
                        strings.append("".join(t.text or "" for t in element.iter(MAIN + "t")))
                        root.clear()
                        if len(strings) > max_cells:
                            raise ValueError("shared_strings_limit")
        with archive.open("xl/workbook.xml") as stream:
            workbook = xml_root(stream)
        with archive.open("xl/_rels/workbook.xml.rels") as stream:
            rels = xml_root(stream)
        relationships = {}
        for rel in rels:
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            relationships[rel.get("Id")] = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target)
        properties = workbook.find(MAIN + "workbookPr")
        epoch = "1904" if properties is not None and properties.get("date1904") in ("1", "true") else "1900"
        sink.write({"record_type": "workbook", "format": "ooxml", "date_system": epoch,
                    "numeric_encoding": "decimal_string", "blank_cells": "absent"})
        sheets = workbook.findall(MAIN + "sheets/" + MAIN + "sheet")
        if len(sheets) > 1000:
            raise ValueError("sheet_count_limit")
        for sheet in sheets:
            name = sheet.get("name")
            target = relationships.get(sheet.get(REL + "id"))
            if not target or not target.startswith("xl/") or target not in archive.namelist():
                raise ValueError("invalid_sheet_relationship")
            sink.write({"record_type": "sheet", "name": name, "state": sheet.get("state", "visible")})
            with archive.open(target) as stream:
                root = None
                for event, cell in xml_events(stream):
                    if root is None:
                        root = cell
                    if event != "end":
                        continue
                    if cell.tag == MAIN + "c":
                        address = cell.get("r")
                        row, col = coordinate(address)
                        value_element, formula_element = cell.find(MAIN + "v"), cell.find(MAIN + "f")
                        raw = value_element.text if value_element is not None else None
                        kind = cell.get("t", "n")
                        value = raw
                        if kind == "s" and raw is not None:
                            string_index = int(raw)
                            if string_index < 0 or string_index >= len(strings):
                                raise ValueError("invalid_shared_string_index")
                            value = strings[string_index]
                        elif kind == "inlineStr":
                            value = "".join(t.text or "" for t in cell.iter(MAIN + "t"))
                        elif kind == "b" and raw is not None:
                            value = raw == "1"
                        elif kind == "str" and value_element is not None:
                            value = raw or ""
                        formula = formula_element is not None
                        if value is not None or formula:
                            if sink.count >= max_cells:
                                raise ValueError("cell_count_limit")
                            cache_known = formula and value is not None
                            sink.write({"record_type": "cell", "sheet": name, "cell": address,
                                "row": row, "column": col, "raw_value": raw, "value": value,
                                "data_type": kind, "style_index": cell.get("s"),
                                "formula": ("=" + (formula_element.text or "")) if formula else None,
                                "formula_attributes": dict(formula_element.attrib) if formula else None,
                                "cached_value": value if formula else None,
                                "cache_status": ("available" if cache_known else "unknown") if formula else "not_formula"})
                            if formula and not cache_known:
                                sink.issue("formula_without_cached_value", sheet=name, cell=address)
                            if formula and formula_element.get("t") == "shared" and not formula_element.text:
                                sink.issue("shared_formula_reference", sheet=name, cell=address)
                            if kind == "e":
                                sink.issue("excel_error", sheet=name, cell=address)
                        cell.clear()
                    elif cell.tag == MAIN + "row":
                        # Remove processed rows so the XML tree cannot grow with workbook size.
                        cell.clear()
                        data = root.find(MAIN + "sheetData")
                        if data is not None:
                            data.clear()
                    elif cell.tag == MAIN + "mergeCell":
                        sink.write({"record_type": "merged_range", "sheet": name, "range": cell.get("ref")})
                        cell.clear()
        # Retain style definitions (dates/formats) as raw XML in the output stream.
        if "xl/styles.xml" in archive.namelist():
            with archive.open("xl/styles.xml") as stream:
                for event, element in xml_events(stream):
                    if event == "end" and element.tag == MAIN + "numFmt":
                        sink.write({"record_type": "number_format", **element.attrib})
                    elif event == "end" and element.tag == MAIN + "cellXfs":
                        for index, style in enumerate(element):
                            sink.write({"record_type": "cell_style", "index": index, **style.attrib})
        if any("externalLink" in i.filename for i in infos):
            sink.issue("external_links_not_resolved")
        if any(i.filename.endswith("vbaProject.bin") for i in infos):
            sink.issue("macros_not_executed")


def _xls(path, sink, max_cells):
    import xlrd
    # xlrd exposes BIFF cached values, not formula expressions. Do not invent them.
    book = xlrd.open_workbook(filename=str(path), on_demand=True, formatting_info=True,
                              ragged_rows=True, use_mmap=True)
    try:
        sink.write({"record_type": "workbook", "format": "biff", "date_system": "1904" if book.datemode else "1900",
                    "numeric_encoding": "ieee754", "blank_cells": "absent"})
        sink.issue("xls_formula_expressions_unavailable")
        area = 0
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            area += sheet.nrows * sheet.ncols
            if area > max_cells:
                raise ValueError("cell_area_limit")
            sink.write({"record_type": "sheet", "name": sheet.name, "rows": sheet.nrows,
                        "columns": sheet.ncols, "visibility": sheet.visibility})
            for rlo, rhi, clo, chi in sheet.merged_cells:
                sink.write({"record_type": "merged_range", "sheet": sheet.name,
                    "range": f"{column_name(clo + 1)}{rlo + 1}:{column_name(chi)}{rhi}"})
            for r in range(sheet.nrows):
                for c, cell in enumerate(sheet.row(r)):
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    if sink.count >= max_cells:
                        raise ValueError("cell_count_limit")
                    if isinstance(cell.value, float) and not math.isfinite(cell.value):
                        raise ValueError("nonfinite_cell_value")
                    address = f"{column_name(c + 1)}{r + 1}"
                    fmt = book.xf_list[cell.xf_index].format_key
                    sink.write({"record_type": "cell", "sheet": sheet.name, "cell": address,
                        "row": r + 1, "column": c + 1, "raw_value": cell.value, "value": cell.value,
                        "data_type": cell.ctype, "number_format": book.format_map[fmt].format_str,
                        "formula": None, "cached_value": None, "cache_status": "formula_provenance_unavailable"})
                    if cell.ctype == xlrd.XL_CELL_ERROR:
                        sink.issue("excel_error", sheet=sheet.name, cell=address)
            book.unload_sheet(index)
    finally:
        book.release_resources()


def _extract_excel(file=None, attachment=None, archive_entry="", max_file_bytes=40_000_000,
                  max_cells=1_000_000, max_uncompressed_bytes=64_000_000,
                  max_result_bytes=100_000_000, preview_cells=20):
    owned, sink, output = [], None, None
    try:
        max_file_bytes = integer(max_file_bytes, "max_file_bytes", 40_000_000, 100_000_000)
        max_cells = integer(max_cells, "max_cells", 1_000_000, 5_000_000)
        max_uncompressed_bytes = integer(max_uncompressed_bytes, "max_uncompressed_bytes", 64_000_000, 256_000_000)
        max_result_bytes = integer(max_result_bytes, "max_result_bytes", 100_000_000, 500_000_000)
        preview_cells = integer(preview_cells, "preview_cells", 20, 100)
        path, filename, sha, attachment_id = _source(file, attachment, archive_entry, max_file_bytes, owned)
        output = temporary(".jsonl")
        sink = Sink(output, max_result_bytes, preview_cells)
        if filename.lower().endswith((".xlsx", ".xlsm")):
            _xlsx(path, sink, max_cells, max_uncompressed_bytes)
        elif filename.lower().endswith(".xls"):
            _xls(path, sink, max_cells)
        else:
            raise ValueError("unsupported_workbook_extension")
        sink.stream.close()
        return {"status": "ok", "cells_file": descriptor(output, "workbook-cells.jsonl", "application/x-ndjson"),
            "source": {"filename": filename, "attachment_id": attachment_id, "sha256": sha},
            "cell_count": sink.count, "cells": sink.preview, "preview_truncated": sink.count > len(sink.preview),
            "warnings": sink.issues, "warning_counts": sink.issue_counts, "errors": [],
            "schema_version": "1.0"}
    except Exception as exc:
        if sink:
            sink.stream.close()
        if output:
            output.unlink(missing_ok=True)
        # Keep library exception strings and file paths out of error outputs.
        safe = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "workbook_read_failed"
        raise ValueError(safe) from None
    finally:
        for path in owned:
            path.unlink(missing_ok=True)


def extract_excel(max_memory_bytes=512_000_000, **inputs):
    limit = integer(max_memory_bytes, "max_memory_bytes", 512_000_000, 2_000_000_000)
    if limit < 64_000_000:
        raise ValueError("invalid_max_memory_bytes")
    with memory_budget(limit) as enforced:
        result = _extract_excel(**inputs)
        result["memory_limit_enforced"] = enforced
        return result


@register_node_class
class ExcelReadNode:
    META = {"name": "ExcelReadNode", "display_name": "Excel Read",
        "description": "Извлекает исходные ячейки, формулы и объединённые диапазоны", "package": "mail_excel",
        "version": "0.2.0", "tags": ["Excel", "File"], "category": "File"}

    def __init__(self, **parameters):
        self.parameters = parameters

    def exec(self, **inputs):
        return extract_excel(**(self.parameters | inputs))


if __name__ == "__main__":
    run_registered_node_from_cli()
