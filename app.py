from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency fallback
    PdfReader = None

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency fallback
    Image = None

APP_DIR = Path(".projectmind")
UPLOAD_DIR = APP_DIR / "uploads"
DB_PATH = APP_DIR / "projectmind.sqlite"
BCA_BLUE = "#003F88"
ACCENT_CYAN = "#00A6D6"
ACCENT_GREEN = "#20A77B"
ACCENT_AMBER = "#F5A623"
LOGIN_USERNAME = "cloverteam"
LOGIN_PASSWORD = "bic2026"

ROLES = ["IT", "UAT", "BA", "PO", "Viewer"]
AGENTS = {
    "Coordinator": {
        "name": "AI Coordinator",
        "domain": "Koordinasi project, blocker, reminder, rekomendasi, dan ringkasan lintas agent.",
    },
    "IT": {
        "name": "Agent IT",
        "domain": "Scope teknis, architecture, flow job/program, dependency, dan runbook.",
    },
    "UAT": {
        "name": "Agent UAT",
        "domain": "Test scenario, defect log, readiness, evidence, dan test script.",
    },
}
LABEL_WEIGHTS = {"Gold": 1.0, "Silver": 0.75, "Bronze": 0.5}


@dataclass
class Source:
    title: str
    label: str
    snippet: str
    score: float
    agent_scope: str


def ensure_storage() -> None:
    APP_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            create table if not exists projects (
                id text primary key,
                name text not null,
                release_id text,
                change_id text,
                description text,
                active_agents text not null,
                created_at text not null
            );

            create table if not exists members (
                id integer primary key autoincrement,
                project_id text not null,
                username text not null,
                role text not null,
                created_at text not null
            );

            create table if not exists documents (
                id text primary key,
                project_id text not null,
                filename text not null,
                source_label text not null,
                agent_scope text not null,
                text text not null,
                created_at text not null
            );

            create table if not exists chats (
                id integer primary key autoincrement,
                project_id text not null,
                agent text not null,
                role text not null,
                content text not null,
                sources text,
                confidence text,
                created_at text not null
            );
            """
        )


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "projectmind"


def stable_id(*parts: str) -> str:
    return hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()[:16]


def db_rows(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(query, params))


def db_execute(query: str, params: tuple = ()) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(query, params)
        conn.commit()


def projects() -> list[sqlite3.Row]:
    return db_rows("select * from projects order by created_at desc")


def create_project(name: str, release_id: str, change_id: str, description: str, active_agents: list[str]) -> str:
    active_agents = normalized_agents(active_agents)
    project_id = f"{slugify(name)}-{stable_id(name, now_iso())[:6]}"
    db_execute(
        """
        insert into projects (id, name, release_id, change_id, description, active_agents, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, name, release_id, change_id, description, json.dumps(active_agents), now_iso()),
    )
    return project_id


def normalized_agents(active_agents: list[str]) -> list[str]:
    ordered = ["Coordinator"]
    for key in active_agents:
        if key in AGENTS and key != "Coordinator" and key not in ordered:
            ordered.append(key)
    return ordered


def seed_sample_project() -> str:
    project_id = create_project(
        "ProjectMind",
        "REL-2026-05",
        "CHG-88021",
        "Koordinasi release QR settlement untuk validasi transaksi, batch monitoring, dan readiness UAT.",
        ["Coordinator", "IT", "UAT"],
    )
    for username, role in [
        ("it.arch01", "IT"),
        ("uat.lead02", "UAT"),
        ("ba.scope03", "BA"),
        ("po.owner04", "PO"),
    ]:
        add_member(project_id, username, role)
    save_document(
        project_id,
        "BRD_scope_release.txt",
        "Gold",
        "All",
        """
        Scope release mencakup perubahan validasi transaksi, update flow program settlement, dan penambahan monitoring batch.
        Dependency utama adalah approval change, kesiapan runbook rollback, dan sign-off UAT untuk scenario regression.
        """,
    )
    save_document(
        project_id,
        "Runbook_deployment.txt",
        "Gold",
        "IT",
        """
        Deployment dilakukan setelah freeze window. Agent IT perlu mengecek dependency job nightly batch, database migration,
        smoke test service, dan rollback script. PIC teknis wajib validasi readiness sebelum release.
        """,
    )
    save_document(
        project_id,
        "UAT_defect_log.csv",
        "Silver",
        "UAT",
        """
        scenario_id,status,severity,owner
        UAT-001,passed,low,uat.lead02
        UAT-002,open,high,it.arch01
        UAT-003,in progress,medium,uat.lead02
        Defect high masih open pada validasi limit transaksi dan perlu retest setelah patch.
        """,
    )
    store_chat(
        project_id,
        "Coordinator",
        "assistant",
        "Saya sudah membaca sumber yang tersedia. Blocker utama saat ini adalah defect high pada validasi limit transaksi dan konfirmasi rollback runbook sebelum freeze window.",
        [
            Source(
                "UAT_defect_log.csv",
                "Silver",
                "Defect high masih open pada validasi limit transaksi dan perlu retest setelah patch.",
                2.0,
                "UAT",
            ),
            Source(
                "Runbook_deployment.txt",
                "Gold",
                "PIC teknis wajib validasi readiness, rollback script, dan dependency job nightly batch sebelum release.",
                2.0,
                "IT",
            ),
        ],
        "High",
    )
    return project_id


def ensure_sample_project() -> None:
    if projects():
        return
    seed_sample_project()


def add_member(project_id: str, username: str, role: str) -> None:
    db_execute(
        "insert into members (project_id, username, role, created_at) values (?, ?, ?, ?)",
        (project_id, username, role, now_iso()),
    )


def project_members(project_id: str) -> list[sqlite3.Row]:
    return db_rows("select * from members where project_id = ? order by role, username", (project_id,))


def project_docs(project_id: str) -> list[sqlite3.Row]:
    return db_rows("select * from documents where project_id = ? order by created_at desc", (project_id,))


def extract_text(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix.lower()
    raw = uploaded_file.getvalue()
    if suffix == ".pdf" and PdfReader:
        temp_path = APP_DIR / f"tmp-{stable_id(uploaded_file.name, str(len(raw)))}.pdf"
        temp_path.write_bytes(raw)
        try:
            reader = PdfReader(str(temp_path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages).strip()
        finally:
            temp_path.unlink(missing_ok=True)
    if suffix in {".xlsx", ".xls"}:
        frames = pd.read_excel(uploaded_file, sheet_name=None)
        lines: list[str] = []
        for sheet_name, frame in frames.items():
            lines.append(f"Sheet: {sheet_name}")
            lines.append(frame.fillna("").to_csv(index=False))
        return "\n".join(lines)
    if suffix == ".csv":
        return pd.read_csv(uploaded_file).fillna("").to_csv(index=False)
    if suffix in {".txt", ".md"}:
        return raw.decode("utf-8", errors="ignore")
    if suffix in {".png", ".jpg", ".jpeg"}:
        if Image:
            image = Image.open(uploaded_file)
            return f"Image file: {uploaded_file.name}. Size: {image.width}x{image.height}. Add manual notes for diagram semantics if OCR is not configured."
        return f"Image file: {uploaded_file.name}. Install Pillow for image metadata extraction."
    return raw.decode("utf-8", errors="ignore")


def chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []
    output = []
    start = 0
    while start < len(normalized):
        output.append(normalized[start : start + size])
        start += size - overlap
    return output


def save_document(project_id: str, filename: str, label: str, agent_scope: str, text: str) -> None:
    doc_id = stable_id(project_id, filename, now_iso())
    path = UPLOAD_DIR / project_id
    path.mkdir(exist_ok=True)
    (path / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    db_execute(
        """
        insert into documents (id, project_id, filename, source_label, agent_scope, text, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, filename, label, agent_scope, text, now_iso()),
    )


def tokenize(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())}


def keyword_search(project_id: str, query: str, agent: str, limit: int = 5) -> list[Source]:
    q_words = tokenize(query)
    if not q_words:
        return []
    results: list[Source] = []
    for doc in project_docs(project_id):
        if agent in {"IT", "UAT"} and doc["agent_scope"] not in {agent, "All"}:
            continue
        for chunk in chunks(doc["text"]):
            c_words = tokenize(chunk)
            overlap = len(q_words & c_words)
            if overlap == 0:
                continue
            score = overlap * LABEL_WEIGHTS.get(doc["source_label"], 0.5)
            results.append(
                Source(
                    title=doc["filename"],
                    label=doc["source_label"],
                    snippet=chunk[:320].replace("\n", " "),
                    score=score,
                    agent_scope=doc["agent_scope"],
                )
            )
    return sorted(results, key=lambda item: item.score, reverse=True)[:limit]


def confidence_for(sources: list[Source]) -> str:
    if not sources:
        return "Low"
    best = sources[0]
    if best.label == "Gold" and best.score >= 2:
        return "High"
    if best.score >= 1.5 or len(sources) >= 2:
        return "Medium"
    return "Low"


def status_tone(value: int) -> str:
    if value >= 75:
        return "green"
    if value >= 55:
        return "amber"
    return "red"


def source_badge(label: str) -> str:
    return {"Gold": "Official", "Silver": "Draft", "Bronze": "Informal"}.get(label, "Source")


def answer_question(project_id: str, agent: str, question: str) -> tuple[str, list[Source], str]:
    sources = keyword_search(project_id, question, agent)
    confidence = confidence_for(sources)
    if agent == "Coordinator":
        answer = coordinator_answer(project_id, question, sources)
    elif sources:
        agent_name = AGENTS[agent]["name"]
        bullets = "\n".join(f"- {source.snippet}" for source in sources[:3])
        answer = (
            f"{agent_name} menemukan konteks paling relevan berikut:\n\n{bullets}\n\n"
            "Rekomendasi: gunakan jawaban ini sebagai asistensi berbasis dokumen, lalu validasi ke PIC bila berdampak ke keputusan project."
        )
    else:
        answer = (
            "Saya belum menemukan konteks yang cukup di knowledge project untuk menjawab dengan yakin. "
            "Tambahkan dokumen resmi atau catatan scope yang lebih spesifik."
        )
    if confidence == "Low":
        answer += "\n\nPerlu validasi oleh PIC terkait"
    return answer, sources, confidence


def coordinator_answer(project_id: str, question: str, sources: list[Source]) -> str:
    members = project_members(project_id)
    docs = project_docs(project_id)
    roles = pd.Series([member["role"] for member in members]).value_counts().to_dict() if members else {}
    labels = pd.Series([doc["source_label"] for doc in docs]).value_counts().to_dict() if docs else {}
    doc_signal = "\n".join(f"- {source.title} ({source.label}): {source.snippet}" for source in sources[:4])
    if not doc_signal:
        doc_signal = "- Belum ada sumber yang match langsung dengan pertanyaan."
    return textwrap.dedent(
        f"""
        Ringkasan AI Coordinator:

        - Komposisi tim saat ini: {roles or "belum ada member"}
        - Knowledge tersedia: {len(docs)} dokumen, distribusi label {labels or "belum ada"}
        - Sinyal lintas-agent terkait pertanyaan:
        {doc_signal}

        Rekomendasi koordinasi:
        - Prioritaskan validasi sumber Gold untuk keputusan release/change.
        - Jika ada gap dokumen UAT atau IT, assign PIC sesuai role sebelum meeting koordinasi berikutnya.
        - Gunakan Agent IT untuk detail teknis dan Agent UAT untuk readiness/defect agar pembahasan tidak tercampur.
        """
    ).strip()


def store_chat(project_id: str, agent: str, role: str, content: str, sources: list[Source] | None = None, confidence: str | None = None) -> None:
    source_payload = json.dumps([source.__dict__ for source in sources or []])
    db_execute(
        """
        insert into chats (project_id, agent, role, content, sources, confidence, created_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, agent, role, content, source_payload, confidence, now_iso()),
    )


def chat_history(project_id: str, agent: str) -> list[sqlite3.Row]:
    return db_rows(
        "select * from chats where project_id = ? and agent = ? order by created_at asc",
        (project_id, agent),
    )


def readiness_scores(project_id: str) -> dict[str, int]:
    docs = project_docs(project_id)
    members = project_members(project_id)
    doc_text = " ".join(doc["text"].lower() for doc in docs)
    has_it = any(doc["agent_scope"] in {"IT", "All"} for doc in docs)
    has_uat = any(doc["agent_scope"] in {"UAT", "All"} for doc in docs)
    has_gold = any(doc["source_label"] == "Gold" for doc in docs)
    defect_signal = any(term in doc_text for term in ["defect", "bug", "failed", "open"])
    return {
        "Knowledge": min(100, 25 + len(docs) * 15 + (20 if has_gold else 0)),
        "Team": min(100, 30 + len(members) * 12 + (20 if {"IT", "UAT"} <= {m["role"] for m in members} else 0)),
        "IT Coverage": 85 if has_it else 35,
        "UAT Coverage": 85 if has_uat else 35,
        "Risk": 45 if defect_signal else 80,
    }


def dashboard_metrics(project_id: str) -> dict[str, str]:
    docs = project_docs(project_id)
    members = project_members(project_id)
    doc_text = " ".join(doc["text"].lower() for doc in docs)
    release_date = datetime(2026, 5, 20).date()
    days_remaining = max((release_date - datetime.now().date()).days, 0)
    open_defects = doc_text.count("open")
    has_it = any(doc["agent_scope"] in {"IT", "All"} for doc in docs)
    has_uat = any(doc["agent_scope"] in {"UAT", "All"} for doc in docs)
    progress = 45 + (15 if has_it else 0) + (15 if has_uat else 0) + min(len(members) * 3, 12)
    if open_defects:
        progress -= 7
    return {
        "Deadline": release_date.strftime("%d %b %Y"),
        "Remaining": f"D-{days_remaining}",
        "Progress": f"{min(progress, 92)}%",
        "Blockers": "2",
        "Open Defects": str(max(open_defects, 1)),
        "Next Gate": "UAT sign-off",
    }


def inject_css() -> None:
    dark = st.session_state.get("dark_mode", False)
    app_bg = "#07111f" if dark else "#f6f8fb"
    sidebar_bg = "#07111f" if dark else "#ffffff"
    card_bg = "#0f1c2e" if dark else "#ffffff"
    card_alt = "#13243a" if dark else "#f9fbfe"
    text_color = "#edf4ff" if dark else "#1f2937"
    muted_color = "#a9b7ca" if dark else "#667085"
    line_color = "#26384f" if dark else "#d7e0eb"
    soft_blue = "#0f2747" if dark else "#eaf3ff"
    input_bg = "#0a1524" if dark else "#ffffff"
    section_color = "#c8d6e8" if dark else "#344054"
    brand_text = "#9CCBFF" if dark else BCA_BLUE
    st.markdown(
        f"""
        <style>
        :root {{
            --bca-blue: {BCA_BLUE};
            --accent-cyan: {ACCENT_CYAN};
            --accent-green: {ACCENT_GREEN};
            --accent-amber: {ACCENT_AMBER};
            --brand: {brand_text};
            --ink: {text_color};
            --muted: {muted_color};
            --line: {line_color};
            --card: {card_bg};
            --card-alt: {card_alt};
            --soft-blue: {soft_blue};
            --input-bg: {input_bg};
            --section: {section_color};
        }}
        .block-container {{
            padding-top: 18px;
            padding-bottom: 28px;
            max-width: 1500px;
        }}
        [data-testid="stHeader"] {{
            background: transparent;
        }}
        .stApp {{
            background:
                linear-gradient(180deg, rgba(0,63,136,.10), transparent 300px),
                linear-gradient(90deg, rgba(0,166,214,.05), transparent 40%),
                {app_bg};
            color: var(--ink);
        }}
        [data-testid="stSidebar"] {{
            background: {sidebar_bg};
            border-right: 1px solid var(--line);
        }}
        [data-testid="stSidebar"] * {{
            color: var(--ink);
        }}
        [data-testid="stSidebar"] .stButton > button {{
            border-radius: 8px;
            border: 1px solid var(--line);
            background: var(--card-alt);
            color: var(--ink);
            min-height: 40px;
        }}
        [data-testid="stSidebar"] .stButton > button:hover {{
            border-color: var(--brand);
            color: var(--brand);
        }}
        [data-baseweb="select"] > div {{
            border-radius: 8px;
            border-color: var(--line);
            background: var(--card-alt);
            color: var(--ink);
        }}
        [data-baseweb="select"] > div:hover {{
            border-color: var(--brand) !important;
        }}
        [data-baseweb="select"] span {{ color: var(--ink); }}
        [data-baseweb="select"] [data-testid="stMarkdownContainer"] p {{ color: var(--ink) !important; }}
        [data-baseweb="tag"] {{
            background: var(--soft-blue) !important;
            border: 1px solid rgba(0,63,136,.18) !important;
        }}
        [data-baseweb="tag"] span {{
            color: var(--brand) !important;
            font-weight: 750;
        }}
        [data-baseweb="tag"] svg {{
            fill: var(--brand) !important;
            color: var(--brand) !important;
        }}
        [data-baseweb="select"] input {{
            color: var(--ink) !important;
            caret-color: var(--ink) !important;
        }}
        [data-baseweb="select"] input::placeholder {{
            color: var(--muted) !important;
        }}
        [data-baseweb="popover"] {{
            background: var(--card) !important;
            border: 1px solid var(--line) !important;
        }}
        [data-baseweb="popover"] [role="option"] {{
            color: var(--ink) !important;
            background: var(--card) !important;
        }}
        [data-baseweb="popover"] [role="option"] * {{
            color: inherit !important;
        }}
        [data-baseweb="popover"] [role="option"]:hover {{
            background: var(--soft-blue) !important;
            color: var(--brand) !important;
        }}
        [data-baseweb="popover"] [role="option"][aria-selected="true"] {{
            background: var(--soft-blue) !important;
            color: var(--brand) !important;
            font-weight: 750;
        }}
        [data-testid="stWidgetLabel"],
        [data-testid="stWidgetLabel"] p,
        [data-testid="stToggle"] label,
        [data-testid="stToggle"] p,
        .stCheckbox label,
        .stCheckbox p {{
            color: var(--ink) !important;
        }}
        input[aria-label="Dark mode"] {{
            accent-color: var(--bca-blue);
            width: 18px;
            height: 18px;
        }}
        label:has(input[aria-label="Dark mode"]) {{
            border: 1px solid var(--line);
            background: var(--card);
            border-radius: 999px;
            padding: 6px 10px;
            color: var(--ink) !important;
        }}
        [role="switch"] {{
            border: 1px solid var(--line) !important;
            background: rgba(102,112,133,.24) !important;
        }}
        [role="switch"][aria-checked="true"] {{
            background: var(--bca-blue) !important;
            border-color: var(--bca-blue) !important;
        }}
        input, textarea {{
            color: var(--ink) !important;
            background: var(--input-bg) !important;
        }}
        input::placeholder, textarea::placeholder {{
            color: var(--muted) !important;
            opacity: 1;
        }}
        .stTextInput input,
        .stTextArea textarea {{
            background: var(--input-bg) !important;
            color: var(--ink) !important;
            border: 1px solid var(--line) !important;
            border-radius: 8px !important;
        }}
        h1, h2, h3 {{
            letter-spacing: 0;
            color: var(--ink);
        }}
        p, li, label, span {{ color: inherit; }}
        .pm-brand {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 2px 0 16px;
        }}
        .pm-logo {{
            width: 42px;
            height: 42px;
            border-radius: 8px;
            display: grid;
            place-items: center;
            color: white;
            font-weight: 900;
            background: linear-gradient(135deg, var(--bca-blue), var(--accent-cyan));
            box-shadow: 0 12px 24px rgba(0,63,136,.24);
        }}
        .pm-brand-title {{
            font-size: 1.05rem;
            font-weight: 850;
            line-height: 1;
        }}
        .pm-brand-subtitle {{
            color: var(--muted);
            font-size: .78rem;
            margin-top: 3px;
        }}
        .pm-topline {{
            border: 1px solid var(--line);
            background:
                linear-gradient(135deg, rgba(0,63,136,.10), transparent 42%),
                var(--card);
            padding: 18px 20px 16px;
            border-radius: 8px;
            margin-bottom: 14px;
            box-shadow: 0 16px 40px rgba(15, 32, 52, .06);
        }}
        .pm-kicker {{
            color: var(--brand);
            font-weight: 800;
            font-size: .78rem;
            text-transform: uppercase;
        }}
        .pm-title-row {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: flex-start;
        }}
        .pm-title-row h1 {{
            font-size: 2rem;
            margin: 4px 0 2px;
        }}
        .pm-tagline {{
            color: var(--muted);
            margin-top: -4px;
        }}
        .pm-chip-row {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 12px;
        }}
        .pm-chip {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 10px;
            border-radius: 999px;
            background: var(--soft-blue);
            border: 1px solid rgba(0,63,136,.16);
            color: var(--brand);
            font-size: .78rem;
            font-weight: 750;
            white-space: nowrap;
        }}
        .pm-live {{
            border: 1px solid rgba(32,167,123,.25);
            background: rgba(32,167,123,.10);
            color: var(--accent-green);
        }}
        .pm-shell {{
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card);
            padding: 16px;
            box-shadow: 0 14px 34px rgba(15,32,52,.05);
        }}
        .pm-section-title {{
            font-size: .78rem;
            font-weight: 850;
            letter-spacing: .04em;
            text-transform: uppercase;
            color: var(--section);
            margin-bottom: 10px;
        }}
        .pm-agent-card {{
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card-alt);
            padding: 13px;
            margin-bottom: 10px;
        }}
        .pm-agent-name {{
            font-weight: 850;
            color: var(--ink);
            margin-bottom: 4px;
        }}
        .pm-agent-domain {{
            color: var(--muted);
            font-size: .86rem;
            line-height: 1.4;
        }}
        .pm-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
            margin: 10px 0 18px;
        }}
        .pm-system {{
            border: 1px solid var(--line);
            background: var(--card);
            border-radius: 8px;
            padding: 12px;
            min-height: 100px;
        }}
        .pm-system strong {{
            display: block;
            margin-bottom: 6px;
        }}
        .pm-status {{
            color: var(--muted);
            font-size: .82rem;
            line-height: 1.35;
        }}
        .pm-metric {{
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 14px;
            background: var(--card);
        }}
        .pm-readiness-grid {{
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 10px;
            margin: 12px 0 16px;
        }}
        .pm-readiness {{
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card);
            padding: 14px;
        }}
        .pm-readiness-label {{
            color: var(--muted);
            font-size: .78rem;
            font-weight: 800;
            text-transform: uppercase;
        }}
        .pm-readiness-value {{
            font-size: 1.55rem;
            font-weight: 900;
            margin-top: 2px;
            color: var(--ink);
        }}
        .pm-dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 10px;
            margin: 10px 0 18px;
        }}
        .pm-dashboard-card {{
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card);
            padding: 14px;
            min-height: 92px;
        }}
        .pm-dashboard-label {{
            color: var(--section);
            font-size: .76rem;
            font-weight: 850;
            text-transform: uppercase;
        }}
        .pm-dashboard-value {{
            color: var(--ink);
            font-size: 1.45rem;
            font-weight: 900;
            margin-top: 8px;
            line-height: 1.1;
        }}
        .pm-progress-line {{
            display: grid;
            grid-template-columns: 140px 1fr 54px;
            gap: 12px;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid var(--line);
        }}
        .pm-progress-line:last-child {{
            border-bottom: 0;
        }}
        .pm-progress-name {{
            color: var(--ink);
            font-weight: 800;
        }}
        .pm-bar {{
            height: 7px;
            background: rgba(102,112,133,.18);
            border-radius: 99px;
            overflow: hidden;
            margin-top: 10px;
        }}
        .pm-fill {{
            height: 100%;
            border-radius: 99px;
        }}
        .pm-fill.green {{ background: var(--accent-green); }}
        .pm-fill.amber {{ background: var(--accent-amber); }}
        .pm-fill.red {{ background: #E55353; }}
        .pm-source {{
            border-left: 4px solid var(--bca-blue);
            padding: 10px 12px;
            background: var(--card);
            border-radius: 4px;
            margin-bottom: 10px;
            font-size: .9rem;
        }}
        .pm-source-title {{
            font-weight: 850;
            color: var(--ink);
            margin: 5px 0 4px;
        }}
        .pm-source-body {{
            color: var(--muted);
            line-height: 1.42;
        }}
        .pm-pill {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background: #e7f0ff;
            color: var(--brand);
            font-size: .78rem;
            font-weight: 700;
        }}
        .pm-pill.gold {{
            background: rgba(245,166,35,.15);
            color: var(--accent-amber);
        }}
        .pm-pill.silver {{
            background: rgba(0,166,214,.14);
            color: var(--accent-cyan);
        }}
        .pm-pill.bronze {{
            background: rgba(102,112,133,.14);
            color: var(--muted);
        }}
        .pm-timeline {{
            border-left: 1px solid var(--line);
            margin: 8px 0 2px 8px;
            padding-left: 14px;
        }}
        .pm-step {{
            position: relative;
            padding-bottom: 11px;
            color: var(--muted);
            font-size: .86rem;
            line-height: 1.35;
        }}
        .pm-step:before {{
            content: "";
            position: absolute;
            left: -19px;
            top: 3px;
            width: 9px;
            height: 9px;
            border-radius: 99px;
            background: var(--bca-blue);
            border: 2px solid var(--card);
        }}
        .pm-action {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 11px 0;
            border-bottom: 1px solid var(--line);
        }}
        .pm-action:last-child {{
            border-bottom: 0;
        }}
        .pm-action strong {{
            color: var(--ink);
        }}
        .pm-action span {{
            color: var(--muted);
            font-size: .84rem;
        }}
        .pm-form-note {{
            color: var(--muted);
            font-size: .9rem;
            margin-bottom: 12px;
        }}
        .pm-login {{
            max-width: 420px;
            margin: 12vh auto 0;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card);
            padding: 26px;
            box-shadow: 0 18px 50px rgba(15,32,52,.10);
        }}
        .pm-login-title {{
            font-size: 1.55rem;
            font-weight: 900;
            color: var(--ink);
            margin: 12px 0 6px;
        }}
        .pm-login-subtitle {{
            color: var(--muted);
            line-height: 1.45;
            margin-bottom: 18px;
        }}
        .pm-userbar {{
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 10px;
        }}
        .pm-user {{
            border: 1px solid var(--line);
            background: var(--card);
            color: var(--ink);
            border-radius: 999px;
            padding: 6px 10px;
            font-weight: 800;
            font-size: .85rem;
        }}
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            border-bottom: 1px solid var(--line);
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 8px 8px 0 0;
            padding: 10px 16px;
            color: var(--muted);
            font-weight: 750;
        }}
        .stTabs [aria-selected="true"] {{
            background: var(--card);
            color: var(--brand);
            border: 1px solid var(--line);
            border-bottom-color: var(--card);
        }}
        .stTabs [aria-selected="true"] p {{
            color: var(--brand);
        }}
        .stTabs [data-baseweb="tab-highlight"] {{
            background-color: var(--bca-blue);
        }}
        [role="radiogroup"] {{
            gap: 8px;
        }}
        [role="radiogroup"] button {{
            border-radius: 8px;
            border: 1px solid var(--line) !important;
            background: var(--card) !important;
            color: var(--ink) !important;
            min-height: 40px;
            padding: 6px 16px;
        }}
        [role="radiogroup"] button[aria-pressed="true"],
        [role="radiogroup"] button[aria-checked="true"] {{
            background: var(--bca-blue) !important;
            border-color: var(--bca-blue) !important;
            color: #ffffff !important;
            font-weight: 850;
        }}
        [role="radiogroup"] button p {{
            color: inherit;
        }}
        div[data-testid="stChatMessage"] {{
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--card);
            padding: 8px 10px;
            box-shadow: none;
        }}
        div[data-testid="stChatMessage"] div,
        div[data-testid="stChatMessage"] p,
        div[data-testid="stChatMessage"] span {{
            color: var(--ink) !important;
        }}
        div[data-testid="stChatMessage"] small {{
            color: var(--muted) !important;
        }}
        div[data-testid="stChatInput"] textarea {{
            border-radius: 8px;
            border: 1px solid var(--line);
            background: var(--input-bg);
            color: var(--ink) !important;
        }}
        div[data-testid="stChatInput"] textarea::placeholder {{
            color: var(--muted) !important;
            opacity: 1 !important;
        }}
        div[data-testid="stChatInput"] {{
            background: transparent !important;
        }}
        [data-testid="stFileUploader"] {{
            color: var(--ink) !important;
        }}
        [data-testid="stFileUploader"] label,
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] p {{
            color: var(--ink) !important;
        }}
        [data-testid="stFileUploader"] small,
        [data-testid="stFileUploader"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"],
        [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] span {{
            color: var(--muted) !important;
        }}
        [data-testid="stFileUploaderDropzone"] {{
            background: var(--card-alt) !important;
            border: 1px dashed var(--line) !important;
            border-radius: 8px !important;
        }}
        [data-testid="stFileUploaderDropzone"]:hover {{
            border-color: var(--brand) !important;
            background: var(--soft-blue) !important;
        }}
        .stButton > button[kind="primary"], .stFormSubmitButton > button {{
            background: var(--bca-blue);
            border: 1px solid var(--bca-blue);
            border-radius: 8px;
            color: #ffffff;
            font-weight: 800;
        }}
        .stButton > button {{
            border-radius: 8px;
            border: 1px solid var(--line);
            background: var(--card);
            color: var(--ink);
            font-weight: 700;
        }}
        .stButton > button:hover {{
            border-color: var(--brand);
            color: var(--brand);
            background: var(--soft-blue);
        }}
        .stButton > button[kind="primary"]:hover,
        .stFormSubmitButton > button:hover {{
            background: #0A4E9D;
            border-color: #0A4E9D;
            color: #ffffff;
        }}
        .stDataFrame, [data-testid="stDataFrame"] {{
            border: 1px solid var(--line);
            border-radius: 8px;
            overflow: hidden;
        }}
        div[data-testid="stMetricValue"] {{
            color: var(--brand);
        }}
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] p {{
            color: var(--section) !important;
            font-weight: 850 !important;
        }}
        @media (max-width: 900px) {{
            .pm-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}
            .pm-readiness-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}
            .pm-dashboard-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}
            .pm-title-row {{
                display: block;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(project: sqlite3.Row | None) -> None:
    title = project["name"] if project else "ProjectMind"
    subtitle = "Project brain with Agentic AI for cross-bureau teams"
    release_id = project["release_id"] if project and project["release_id"] else "Release TBD"
    change_id = project["change_id"] if project and project["change_id"] else "Change TBD"
    description = project["description"] if project and project["description"] else "Workspace kolaborasi lintas biro berbasis dokumen resmi project."
    st.markdown(
        f"""
        <div class="pm-topline">
            <div class="pm-title-row">
                <div>
                    <div class="pm-kicker">BCA Internal Project Workspace</div>
                    <h1>{title}</h1>
                    <div class="pm-tagline">{subtitle}</div>
                </div>
                <div class="pm-chip-row">
                    <span class="pm-chip">{release_id}</span>
                    <span class="pm-chip">{change_id}</span>
                    <span class="pm-chip pm-live">Release watch</span>
                </div>
            </div>
            <div class="pm-tagline" style="margin-top: 10px;">{description}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_login() -> None:
    st.markdown(
        """
        <div class="pm-login">
            <div class="pm-logo">PM</div>
            <div class="pm-login-title">ProjectMind</div>
            <div class="pm-login-subtitle">Masuk ke workspace project internal.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 0.9, 1])
    with center:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", width="stretch")
        if submitted:
            if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                st.rerun()
            else:
                st.error("Username atau password tidak sesuai.")


def render_project_creator() -> str | None:
    st.subheader("Create ProjectMind Workspace")
    with st.form("create_project"):
        name = st.text_input("Nama Project", placeholder="Contoh: QR Payment Settlement Release")
        col1, col2 = st.columns(2)
        release_id = col1.text_input("Release ID", placeholder="REL-2026-05")
        change_id = col2.text_input("Change ID", placeholder="CHG-88021")
        description = st.text_area("Deskripsi / scope / catatan penting", placeholder="Scope, background, dependency, dan catatan koordinasi awal.")
        st.caption("AI Coordinator aktif secara default sebagai otak workspace.")
        optional_agents = st.multiselect(
            "Agent Spesialis Aktif",
            ["IT", "UAT"],
            default=["IT", "UAT"],
            format_func=lambda key: AGENTS[key]["name"],
        )
        submitted = st.form_submit_button("Create workspace", width="stretch")
    if submitted and name.strip():
        return create_project(name.strip(), release_id.strip(), change_id.strip(), description.strip(), optional_agents)
    if submitted:
        st.warning("Nama project wajib diisi.")
    return None


def render_sidebar() -> sqlite3.Row | None:
    create_mode = st.session_state.get("show_create", False)
    st.sidebar.markdown(
        """
        <div class="pm-brand">
            <div class="pm-logo">PM</div>
            <div>
                <div class="pm-brand-title">ProjectMind</div>
                <div class="pm-brand-subtitle">Agentic project brain</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    items = projects()
    selected_project = None
    if items and not create_mode:
        ids = [item["id"] for item in items]
        default_id = st.session_state.get("project_id", ids[0])
        selected_id = st.sidebar.selectbox(
            "Workspace",
            ids,
            index=ids.index(default_id) if default_id in ids else 0,
            format_func=lambda project_id: next(item["name"] for item in items if item["id"] == project_id),
        )
        st.session_state["project_id"] = selected_id
        selected_project = next(item for item in items if item["id"] == selected_id)
    elif not items:
        st.sidebar.info("Belum ada workspace.")
    else:
        st.sidebar.caption("Mode create aktif. Pilih workspace setelah project dibuat.")
    if st.sidebar.button("New ProjectMind", width="stretch"):
        st.session_state["show_create"] = True
        st.rerun()
    if create_mode and st.sidebar.button("Back to Workspace", width="stretch"):
        st.session_state["show_create"] = False
        st.rerun()
    st.sidebar.divider()
    if selected_project and not create_mode:
        active_agents = normalized_agents(json.loads(selected_project["active_agents"]))
        st.sidebar.markdown('<div class="pm-section-title">Active Agents</div>', unsafe_allow_html=True)
        for key in active_agents:
            st.sidebar.markdown(
                f"""
                <div class="pm-agent-card">
                    <div class="pm-agent-name">{AGENTS[key]["name"]}</div>
                    <div class="pm-agent-domain">{AGENTS[key]["domain"]}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    return selected_project


def render_top_controls() -> None:
    left, mid, right = st.columns([1, 0.2, 0.14])
    with mid:
        st.toggle("Dark mode", key="dark_mode")
    with right:
        if st.session_state.get("authenticated"):
            if st.button("Logout", width="stretch"):
                st.session_state["authenticated"] = False
                st.rerun()


def render_members(project_id: str) -> None:
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.subheader("Project Members")
        members = project_members(project_id)
        if members:
            st.dataframe(pd.DataFrame([dict(member) for member in members])[["username", "role", "created_at"]], hide_index=True, width="stretch")
        else:
            st.info("Belum ada member di workspace ini.")
    with right:
        st.subheader("Invite Member")
        st.markdown(
            '<div class="pm-form-note">Tambahkan username dan role project.</div>',
            unsafe_allow_html=True,
        )
        with st.form("add_member", clear_on_submit=True):
            username = st.text_input("Username internal BCA", placeholder="contoh: bca12345")
            role = st.selectbox("Role", ROLES)
            submitted = st.form_submit_button("Invite member", width="stretch")
        if submitted and username.strip():
            add_member(project_id, username.strip(), role)
            st.rerun()


def render_knowledge(project_id: str) -> None:
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        st.subheader("Knowledge Intake")
        with st.form("upload_knowledge", clear_on_submit=True):
            uploaded = st.file_uploader("Upload PDF, Excel/CSV, text, atau image", type=["pdf", "xlsx", "xls", "csv", "txt", "md", "png", "jpg", "jpeg"])
            col1, col2 = st.columns(2)
            label = col1.selectbox("Trust label", ["Gold", "Silver", "Bronze"])
            scope = col2.selectbox("Agent yang memanfaatkan", ["All", "IT", "UAT"])
            manual_notes = st.text_area("Catatan tambahan opsional", placeholder="Tambahkan konteks manual untuk flow diagram, change note, atau scope narasi.")
            submitted = st.form_submit_button("Index knowledge")
        if submitted and uploaded:
            extracted = extract_text(uploaded)
            text = "\n\n".join(part for part in [extracted, manual_notes.strip()] if part)
            if text.strip():
                save_document(project_id, uploaded.name, label, scope, text)
                st.success(f"{uploaded.name} berhasil ditambahkan.")
                st.rerun()
            else:
                st.warning("Tidak ada teks yang bisa di-index dari file tersebut.")
    with right:
        st.markdown('<div class="pm-section-title">Source Mix</div>', unsafe_allow_html=True)
        docs_for_mix = project_docs(project_id)
        labels = pd.Series([doc["source_label"] for doc in docs_for_mix]).value_counts().to_dict() if docs_for_mix else {}
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Gold", labels.get("Gold", 0))
        col_b.metric("Silver", labels.get("Silver", 0))
        col_c.metric("Bronze", labels.get("Bronze", 0))
        st.markdown('<div class="pm-section-title" style="margin-top: 18px;">Recent Sources</div>', unsafe_allow_html=True)
        for doc in docs_for_mix[:4]:
            st.markdown(
                f"""
                <div class="pm-source">
                    <span class="pm-pill {doc["source_label"].lower()}">{doc["source_label"]}</span>
                    <div class="pm-source-title">{doc["filename"]}</div>
                    <div class="pm-source-body">{doc["agent_scope"]} · {doc["created_at"]}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    docs = project_docs(project_id)
    if docs:
        st.subheader("Indexed Sources")
        st.dataframe(
            pd.DataFrame([dict(doc) for doc in docs])[["filename", "source_label", "agent_scope", "created_at"]],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Belum ada knowledge. Upload dokumen resmi project untuk menaikkan confidence agent.")


def render_readiness_strip(scores: dict[str, int]) -> None:
    cards = []
    for label, value in scores.items():
        tone = status_tone(value)
        cards.append(
            f'<div class="pm-readiness"><div class="pm-readiness-label">{label}</div><div class="pm-readiness-value">{value}%</div><div class="pm-bar"><div class="pm-fill {tone}" style="width:{value}%"></div></div></div>'
        )
    st.markdown(f'<div class="pm-readiness-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_dashboard_cards(metrics: dict[str, str]) -> None:
    cards = []
    for label, value in metrics.items():
        cards.append(
            f'<div class="pm-dashboard-card"><div class="pm-dashboard-label">{label}</div><div class="pm-dashboard-value">{value}</div></div>'
        )
    st.markdown(f'<div class="pm-dashboard-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_chat(project: sqlite3.Row) -> None:
    active_agents = normalized_agents(json.loads(project["active_agents"]))
    if not active_agents:
        st.info("Aktifkan minimal satu agent di workspace.")
        return
    agent = st.segmented_control(
        "Ask an agent",
        active_agents,
        default=active_agents[0],
        format_func=lambda key: AGENTS[key]["name"],
    )
    left, right = st.columns([2.05, 1], gap="large")
    with left:
        st.markdown(f'<div class="pm-section-title">{AGENTS[agent]["name"]}</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="pm-agent-card" style="margin-bottom: 14px;">
                <div class="pm-agent-name">Domain contract</div>
                <div class="pm-agent-domain">{AGENTS[agent]["domain"]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        for message in chat_history(project["id"], agent):
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message["confidence"]:
                    st.caption(f"Confidence: {message['confidence']}")
        prompt = st.chat_input(f"Tanya {AGENTS[agent]['name']}")
        if prompt:
            store_chat(project["id"], agent, "user", prompt)
            answer, sources, confidence = answer_question(project["id"], agent, prompt)
            store_chat(project["id"], agent, "assistant", answer, sources, confidence)
            st.rerun()
    with right:
        st.markdown('<div class="pm-section-title">Source Reference</div>', unsafe_allow_html=True)
        latest = db_rows(
            """
            select * from chats
            where project_id = ? and agent = ? and role = 'assistant'
            order by created_at desc limit 1
            """,
            (project["id"], agent),
        )
        if latest and latest[0]["sources"]:
            if latest[0]["confidence"]:
                st.markdown(f'<span class="pm-chip">Confidence: {latest[0]["confidence"]}</span>', unsafe_allow_html=True)
            for source in json.loads(latest[0]["sources"])[:5]:
                label_class = source["label"].lower()
                st.markdown(
                    f"""
                    <div class="pm-source">
                        <span class="pm-pill {label_class}">{source_badge(source["label"])} · {source["label"]}</span>
                        <div class="pm-source-title">{source["title"]}</div>
                        <div class="pm-source-body">{source["snippet"]}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("Referensi sumber akan muncul setelah agent menjawab.")


def render_dashboard(project: sqlite3.Row) -> None:
    metrics = dashboard_metrics(project["id"])
    st.subheader("Project Dashboard")
    render_dashboard_cards(metrics)
    docs = project_docs(project["id"])
    chats = db_rows("select * from chats where project_id = ?", (project["id"],))
    col1, col2 = st.columns([1.2, 1], gap="large")
    with col1:
        st.markdown('<div class="pm-section-title">Milestone Progress</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="pm-progress-line"><div class="pm-progress-name">Scope freeze</div><div class="pm-bar"><div class="pm-fill green" style="width:100%"></div></div><div>Done</div></div>
            <div class="pm-progress-line"><div class="pm-progress-name">IT readiness</div><div class="pm-bar"><div class="pm-fill green" style="width:82%"></div></div><div>82%</div></div>
            <div class="pm-progress-line"><div class="pm-progress-name">UAT execution</div><div class="pm-bar"><div class="pm-fill amber" style="width:64%"></div></div><div>64%</div></div>
            <div class="pm-progress-line"><div class="pm-progress-name">Release approval</div><div class="pm-bar"><div class="pm-fill red" style="width:35%"></div></div><div>35%</div></div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div class="pm-section-title" style="margin-top: 22px;">Coordinator Action Queue</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="pm-action"><div><strong>Validate high defect on transaction limit</strong><br/><span>Owner: IT + UAT</span></div><span>Today</span></div>
            <div class="pm-action"><div><strong>Confirm rollback runbook and nightly batch dependency</strong><br/><span>Owner: IT</span></div><span>Before freeze</span></div>
            <div class="pm-action"><div><strong>Collect UAT sign-off evidence</strong><br/><span>Owner: UAT</span></div><span>Pending</span></div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Generate coordinator summary", type="primary"):
            answer, sources, confidence = answer_question(project["id"], "Coordinator", "summary readiness blocker progress")
            store_chat(project["id"], "Coordinator", "assistant", answer, sources, confidence)
            st.success("Summary tersimpan di histori AI Coordinator.")
    with col2:
        st.markdown('<div class="pm-section-title">Workspace Signals</div>', unsafe_allow_html=True)
        metric1, metric2, metric3 = st.columns(3)
        metric1.metric("Sources", len(docs))
        metric2.metric("Members", len(project_members(project["id"])))
        metric3.metric("Chats", len(chats))
        st.markdown('<div class="pm-section-title" style="margin-top: 18px;">Blockers</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="pm-source">
                <span class="pm-pill silver">High</span>
                <div class="pm-source-title">UAT-002 transaction limit validation</div>
                <div class="pm-source-body">Patch needs retest before sign-off.</div>
            </div>
            <div class="pm-source">
                <span class="pm-pill gold">Medium</span>
                <div class="pm-source-title">Rollback runbook confirmation</div>
                <div class="pm-source-body">Nightly batch dependency must be validated before freeze.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div class="pm-section-title" style="margin-top: 18px;">Recent Activity</div>', unsafe_allow_html=True)
        for chat in chats[-3:]:
            st.markdown(
                f"""
                <div class="pm-action">
                    <div><strong>{chat["agent"]} · {chat["role"]}</strong><br/><span>{chat["content"][:90]}</span></div>
                    <span>{chat["created_at"].split("T")[-1]}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )


def main() -> None:
    st.set_page_config(page_title="ProjectMind", page_icon="PM", layout="wide", initial_sidebar_state="expanded")
    st.session_state.setdefault("dark_mode", False)
    inject_css()
    if not st.session_state.get("authenticated"):
        render_login()
        return
    ensure_storage()
    project = render_sidebar()
    render_top_controls()
    if st.session_state.get("show_create"):
        render_header(None)
        new_id = render_project_creator()
        if new_id:
            st.session_state["project_id"] = new_id
            st.session_state["show_create"] = False
            st.rerun()
        return
    if not project:
        render_header(None)
        new_id = render_project_creator()
        if new_id:
            st.session_state["project_id"] = new_id
            st.session_state["show_create"] = False
            st.rerun()
        return
    render_header(project)
    tab_chat, tab_knowledge, tab_members, tab_dashboard = st.tabs(["Chat", "Knowledge", "Members", "Dashboard"])
    with tab_chat:
        render_chat(project)
    with tab_knowledge:
        render_knowledge(project["id"])
    with tab_members:
        render_members(project["id"])
    with tab_dashboard:
        render_dashboard(project)


if __name__ == "__main__":
    main()
