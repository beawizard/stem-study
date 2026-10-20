"""Contest math papers: Vedic (PNVMO / IVMO) scoring and ISMC flat scoring."""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{W_NS}}}p"
W_T = f"{{{W_NS}}}t"

_Q_HEAD = re.compile(
    r"^(\d{1,3})[.)]\s+(.*?)(?:\s*\[(\d+)\s*pts?\])?\s*$",
    re.IGNORECASE,
)
_CHOICE = re.compile(r"^([A-E])[.)]?\s+(.+)$")
_ANS = re.compile(r"\[ans\s*=\s*([A-E])\]", re.IGNORECASE)
_SKIP = re.compile(
    r"^(questions\s+\d|paper\s+[a-z]|set\s+\d|time allowed|quick letter|letter key|"
    r"maximum\s+\d+|primary|beginners|11 years)",
    re.IGNORECASE,
)
_ANSWER_KEY_LINE = re.compile(
    r"^\d{1,3}[A-E](\s+\d{1,3}[A-E])+\s*$",
    re.IGNORECASE,
)

# Official exam format (current PNVMO / IVMO style)
# Q1–25: 2 marks, no penalty. Q26–35: 3 marks, −1 if wrong.
# Q36–40: 4 marks, −2 if wrong. Blank = 0. Maximum 100.
MAX_SCORE = 100
TIME_LIMIT_SEC = 3600

# International Speed Math Challenge: same paper layout, flat scoring.
# 1 correct answer = 1 point. Wrong and blank score 0. No Vedic penalties.
ISMC_POINT = 1
ISMC_DEFAULT_TIME_LIMIT_SEC = 12 * 60

_VEDIC_SCHEME = re.compile(
    r"(\d+\s*marks|\bpenalty\b|\[\d+\s*pts?\]|maximum\s+\d+|paper\s+[a-z])",
    re.IGNORECASE,
)
_ISMC_LEVEL = re.compile(r"(^|\n)\s*level\s+\d+\b", re.IGNORECASE)
_TIME_MIN = re.compile(r"time\s+allowed:\s*(\d+)\s*minutes\b", re.IGNORECASE)
_TIME_HOUR = re.compile(r"time\s+allowed:\s*(\d+)\s*hours?\b", re.IGNORECASE)
_QCOUNT = re.compile(r"(\d+)\s+multiple-choice\s+questions", re.IGNORECASE)
_KEY_PAIR = re.compile(r"(\d{1,3})([A-E])", re.IGNORECASE)


def official_points_penalty(item_no: int) -> tuple[int, int]:
    n = int(item_no)
    if n <= 25:
        return 2, 0
    if n <= 35:
        return 3, 1
    return 4, 2


def extract_docx_paragraphs(data: bytes) -> list[str]:
    """Read visible paragraph text from a .docx using stdlib zip+xml."""
    if not data:
        raise ValueError("Empty document")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid .docx file") from exc
    try:
        xml = zf.read("word/document.xml")
    except KeyError as exc:
        raise ValueError("Not a valid .docx file (missing document.xml)") from exc
    root = ET.fromstring(xml)
    paras: list[str] = []
    for p in root.iter(W_P):
        parts = [(t.text or "") for t in p.iter(W_T)]
        text = "".join(parts).strip()
        if text:
            paras.append(text)
    return paras


def looks_like_vedic(text: str) -> bool:
    if not text:
        return False
    if "[ans=" in text.lower():
        return True
    letters = sum(1 for ln in text.splitlines() if _CHOICE.match(ln.strip()))
    return letters >= 10 and bool(_Q_HEAD.search(text))


def _head_lines(paragraphs: list[str]) -> list[str]:
    """Preamble lines before the first numbered item."""
    head: list[str] = []
    for raw in paragraphs:
        line = (raw or "").strip()
        if not line:
            continue
        if _Q_HEAD.match(line):
            break
        head.append(line)
    return head


def detect_exam_kind(paragraphs: list[str]) -> str:
    """Classify a contest paper.

    Vedic when the preamble states mark bands, penalties, or ``[N pts]``.
    ISMC when it is a timed LEVEL paper without that scheme.
    Bare MCQ text (no header) stays Vedic so existing papers keep their scoring.
    """
    head = "\n".join(_head_lines(paragraphs))
    if _VEDIC_SCHEME.search(head):
        return "vedic"
    for raw in paragraphs:
        if re.search(r"\[\d+\s*pts?\]", raw or "", re.IGNORECASE):
            return "vedic"
    if _ISMC_LEVEL.search(head) or _TIME_MIN.search(head):
        return "ismc"
    return "vedic"


def looks_like_ismc(text: str) -> bool:
    if not text or not looks_like_vedic(text):
        return False
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    return detect_exam_kind(lines) == "ismc"


def ismc_time_limit_sec(paragraphs: list[str]) -> int:
    head = "\n".join(_head_lines(paragraphs))
    minutes = _TIME_MIN.search(head)
    if minutes:
        return max(1, int(minutes.group(1))) * 60
    hours = _TIME_HOUR.search(head)
    if hours:
        return max(1, int(hours.group(1))) * 3600
    return ISMC_DEFAULT_TIME_LIMIT_SEC


def _header_question_count(paragraphs: list[str]) -> int | None:
    match = _QCOUNT.search("\n".join(_head_lines(paragraphs)))
    return int(match.group(1)) if match else None


def _answer_key_map(paragraphs: list[str]) -> dict[int, str]:
    for raw in paragraphs:
        line = (raw or "").strip()
        if not _ANSWER_KEY_LINE.match(line):
            continue
        return {int(n): letter.upper() for n, letter in _KEY_PAIR.findall(line)}
    return {}


def parse_vedic_mcq(paragraphs: list[str]) -> list[dict[str, Any]]:
    """Parse a Vedic paper: numbered stem, A–E choices, official mark bands."""
    return _parse_contest_mcq(paragraphs, scoring="vedic")


def parse_ismc_mcq(paragraphs: list[str]) -> list[dict[str, Any]]:
    """Parse an ISMC paper. Every item is worth 1 point, with no penalty."""
    items = _parse_contest_mcq(paragraphs, scoring="ismc")
    key = _answer_key_map(paragraphs)
    if key:
        for item in items:
            number = int(item["item_no"])
            if number not in key:
                raise ValueError(f"Question {number} is missing from the answer key")
            if key[number] != item["answer"]:
                raise ValueError(
                    f"Question {number}: [ans={item['answer']}] does not match "
                    f"answer key {key[number]}"
                )
    expected = _header_question_count(paragraphs)
    if expected is not None and len(items) != expected:
        raise ValueError(f"Expected {expected} questions, found {len(items)}")
    return items


def _parse_contest_mcq(
    paragraphs: list[str], *, scoring: str = "vedic"
) -> list[dict[str, Any]]:
    """Parse official-style items: numbered stem, A–E choices, [ans=X]."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush(*, require: bool = False) -> None:
        nonlocal current
        if not current:
            return
        choices = current.get("choices") or []
        ans = str(current.get("answer") or "").strip().upper()
        keys = {c["key"] for c in choices}
        # Header/preamble can look like "1. …"; drop stubs that are not real items.
        if len(choices) < 2 or ans not in keys:
            if require:
                raise ValueError(
                    f"Question {current.get('item_no')}: need A–E choices and [ans=letter]"
                )
            current = None
            return
        if scoring == "ismc":
            pts, pen = ISMC_POINT, 0
        else:
            pts, pen = official_points_penalty(int(current["item_no"]))
            tagged = current.get("tagged_points")
            if tagged:
                pts = int(tagged)
        current["points"] = pts
        current["penalty"] = pen
        current["qtype"] = "mcq"
        items.append(current)
        current = None

    for raw in paragraphs:
        line = (raw or "").strip()
        if not line or _SKIP.match(line) or _ANSWER_KEY_LINE.match(line):
            continue
        m_ans = _ANS.search(line)
        if m_ans and current:
            current["answer"] = m_ans.group(1).upper()
            # Stem may include [ans=X] on the same line as the last choice.
            rest = _ANS.sub("", line).strip()
            m_c = _CHOICE.match(rest)
            if m_c:
                current["choices"].append(
                    {"key": m_c.group(1).upper(), "text": m_c.group(2).strip()}
                )
            flush()
            continue
        m_q = _Q_HEAD.match(line)
        if m_q:
            flush()
            item_no = int(m_q.group(1))
            prompt = (m_q.group(2) or "").strip()
            tagged = int(m_q.group(3)) if m_q.group(3) else None
            current = {
                "item_no": item_no,
                "prompt": prompt,
                "tagged_points": tagged,
                "choices": [],
                "answer": "",
            }
            continue
        m_c = _CHOICE.match(line)
        if m_c and current:
            current["choices"].append(
                {"key": m_c.group(1).upper(), "text": m_c.group(2).strip()}
            )
            continue

    flush()
    if not items:
        label = "ISMC" if scoring == "ismc" else "Vedic"
        raise ValueError(f"No {label} multiple-choice questions found in the document")
    return items


def score_vedic_item(
    *,
    item_no: int,
    expected: str,
    given: str,
    points: int | None = None,
    penalty: int | None = None,
) -> dict[str, Any]:
    """Score one item. Blank is 0 (no penalty). Wrong applies official penalty."""
    pts, pen = official_points_penalty(item_no)
    if points is not None:
        pts = int(points)
    if penalty is not None:
        pen = int(penalty)
    exp = str(expected or "").strip().upper()[:1]
    got = str(given or "").strip().upper()[:1]
    if not got:
        return {
            "correct": False,
            "skipped": True,
            "marks": 0,
            "points": pts,
            "penalty": pen,
        }
    if got == exp:
        return {
            "correct": True,
            "skipped": False,
            "marks": pts,
            "points": pts,
            "penalty": pen,
        }
    return {
        "correct": False,
        "skipped": False,
        "marks": -pen,
        "points": pts,
        "penalty": pen,
    }


def score_ismc_item(
    *,
    expected: str,
    given: str,
    points: int | None = None,
) -> dict[str, Any]:
    """Score one ISMC item. Correct is 1 point. Wrong and blank are 0."""
    pts = ISMC_POINT if points is None else int(points)
    exp = str(expected or "").strip().upper()[:1]
    got = str(given or "").strip().upper()[:1]
    if not got:
        return {
            "correct": False,
            "skipped": True,
            "marks": 0,
            "points": pts,
            "penalty": 0,
        }
    if got == exp:
        return {
            "correct": True,
            "skipped": False,
            "marks": pts,
            "points": pts,
            "penalty": 0,
        }
    return {
        "correct": False,
        "skipped": False,
        "marks": 0,
        "points": pts,
        "penalty": 0,
    }
