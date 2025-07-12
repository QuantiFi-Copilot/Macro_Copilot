# universal_agent.py  (Python ≥ 3.9)
import io, re, subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import camelot, pymupdf, pdfplumber, pytesseract, layoutparser as lp
import pandas as pd
from rapidfuzz import fuzz, process as rf_process

# ── Models ─────────────────────────────────────────────────────────────
@dataclass
class FinancialMetric:
    metric_name: str                # canonical name
    raw_name: str                   # as it appeared in PDF
    values: Dict[str, Optional[float]]

@dataclass
class EarningsExtraction:
    source_file: str
    company_name: Optional[str]
    period: Optional[str]
    key_financials: List[FinancialMetric]

# ── Constants / Config ─────────────────────────────────────────────────
SCORE_THRESHOLD   = 60          # min RapidFuzz ratio to accept table
METRIC_GLOSSARY   = {
    # canonical : [possible aliases...]
    "Gross Revenue"    : ["gross revenue", "total income", "revenue from operations"],
    "EBITDA"           : ["ebitda", "operating profit before depreciation",
                          "earnings before interest"],
    "PBT"              : ["profit before tax"],
    "PAT"              : ["profit after tax", "net profit", "profit for the period",
                          "net income"],
    "EPS"              : ["earnings per share"],
    "Net Debt"         : ["net debt", "net borrowings"],
}
KEYWORDS_TABLE_LIKELY = set(k.lower() for k in METRIC_GLOSSARY.keys())

NUM_RE = re.compile(r"[-–]?\s*\(?\d[\d,.]*\)?")

# ── Helper utils───────────────────────────────────────────────────────
def parse_number(txt: str) -> Optional[float]:
    """Parse (), minus, commas, spaces."""
    if not txt or not NUM_RE.search(txt):
        return None
    first = NUM_RE.search(txt).group()
    clean = first.replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(clean)
    except ValueError:
        return None

def clean(cell) -> str:
    return " ".join(str(cell).split()).strip()

def normalise_metric(name: str) -> Tuple[str, str]:
    name_low = name.lower()
    for canon, aliases in METRIC_GLOSSARY.items():
        for alias in [canon] + aliases:
            if fuzz.partial_ratio(name_low, alias) > 90:
                return canon, name
    return name, name  # unknown → keep raw

# ── Core class ─────────────────────────────────────────────────────────
class UniversalEarningsAgent:
    def __init__(self, pdf_path: Path):
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        self.pdf_path = pdf_path
        self.doc = pymupdf.open(pdf_path)

    # — 1.  Pull every table candidate via 4 extractors  ————————
    def _extract_tables_all(self) -> List[pd.DataFrame]:
        dfs: List[pd.DataFrame] = []

        pages = ",".join(map(str, range(1, self.doc.page_count + 1)))
        # a) Camelot lattice
        try:
            dfs += [t.df for t in camelot.read_pdf(
                str(self.pdf_path), pages=pages, flavor="lattice",
                strip_text="\n", suppress_stdout=True)]
        except Exception:
            pass
        # b) Camelot stream
        try:
            dfs += [t.df for t in camelot.read_pdf(
                str(self.pdf_path), pages=pages, flavor="stream",
                strip_text="\n", suppress_stdout=True)]
        except Exception:
            pass
        # c) pdfplumber
        with pdfplumber.open(self.pdf_path) as pdf:
            for pg in pdf.pages:
                tbls = pg.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3})
                for tbl in tbls:
                    dfs.append(pd.DataFrame(tbl))
        # d) OCR (layout-parser) for image-only pages
        dfs += self._extract_tables_via_ocr()

        return dfs

    def _extract_tables_via_ocr(self) -> List[pd.DataFrame]:
        dfs = []
        model = lp.PaddleDetectionLayoutModel(
            config_path="lp://PubLayNet/faster_rcnn_R_50_FPN_3x/config",
            label_map={0: "table"}, extra_config={"batch_size": 2})
        for i in range(self.doc.page_count):
            pix = self.doc.load_page(i).get_pixmap(dpi=300)
            image = lp.load_image(io.BytesIO(pix.tobytes("png")))
            layout = model.detect(image)
            for block in layout:
                if block.type == "table":
                    x1, y1, x2, y2 = map(int, block.coordinates)
                    crop = image[y1:y2, x1:x2]
                    ocr_txt = pytesseract.image_to_string(crop)
                    rows = [row.split() for row in ocr_txt.splitlines() if row.strip()]
                    if rows:
                        dfs.append(pd.DataFrame(rows))
        return dfs

    # — 2.  Score each table & choose the best candidate ————————
    def _select_financial_table(self, dfs: List[pd.DataFrame]) -> pd.DataFrame:
        scored = []
        for df in dfs:
            flat = " ".join(df.astype(str).values.flatten()).lower()
            hit = sum(1 for kw in KEYWORDS_TABLE_LIKELY if kw in flat)
            scored.append((hit, len(df), df))
        if not scored:
            raise RuntimeError("No tables detected at all.")

        scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
        best_hit, _, best_df = scored[0]
        if best_hit == 0:
            raise RuntimeError("None of the tables look like financial highlights.")
        return best_df

    # — 3.  Parse the chosen DataFrame into structured metrics ————
    def _parse_table(self, df: pd.DataFrame) -> List[FinancialMetric]:
        df = df.replace("", None).dropna(how="all").dropna(axis=1, how="all")
        if df.empty or df.shape[1] < 3:
            raise ValueError("Selected table is too small.")

        # find header row
        header_idx = next((i for i, row in df.iterrows()
                          if re.search(r"FY\d{2}|Q[1-4]", " ".join(map(str, row)))),
                          None)
        if header_idx is None:
            raise ValueError("Could not find header row.")

        header_raw = [clean(c) for c in df.iloc[header_idx].tolist()]
        periods = [h for h in header_raw if h and not re.match(r"%|chg", h, re.I)][2:]
        period_scale = self._detect_scale(" ".join(header_raw))

        metrics: List[FinancialMetric] = []
        for _, row in df.iloc[header_idx + 1:].iterrows():
            raw_name = clean(row.iloc[1])
            if not raw_name:
                continue
            canon_name, _ = normalise_metric(raw_name)

            nums: List[str] = []
            for cell in row.iloc[2:]:
                nums += NUM_RE.findall(str(cell))
            values = {
                p: (parse_number(nums[i]) * period_scale if i < len(nums) else None)
                for i, p in enumerate(periods)}
            metrics.append(FinancialMetric(metric_name=canon_name,
                                           raw_name=raw_name,
                                           values=values))
        return metrics

    @staticmethod
    def _detect_scale(text: str) -> float:
        """₹ crore → 1, ₹ million → .1, etc.  Adjust as needed."""
        text_low = text.lower()
        if "million" in text_low or "mn" in text_low:
            return 0.1   # 1 crore = 10 million
        elif "billion" in text_low or "bn" in text_low:
            return 100   # 1 bn = 100 crore
        return 1.0

    # — 4.  Public API ————————————————————————————————————————————
    def run(self) -> EarningsExtraction:
        all_dfs  = self._extract_tables_all()
        fin_df   = self._select_financial_table(all_dfs)
        metrics  = self._parse_table(fin_df)

        # crude company / period scrape (improve as needed)
        first_pg = self.doc.load_page(0).get_text()
        company  = re.search(r"([A-Z][A-Za-z &]+) Limited", first_pg)
        period   = re.search(r"(Q[1-4]\s*FY\d{2})", first_pg)
        return EarningsExtraction(
            source_file=self.pdf_path.name,
            company_name=company.group(1) if company else None,
            period=period.group(1) if period else None,
            key_financials=metrics,
        )