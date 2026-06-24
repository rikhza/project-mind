from __future__ import annotations

import hashlib
import html
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

ROLES = ["IT", "BA", "UAT", "PO"]
UNITS = ["ITX IDS", "UAT B", "SSI D"]

NIP_MAPPING = {
    "12345678": {"nama": "Budi Santoso", "username": "budi.santoso"},
    "23456789": {"nama": "Andi Pratama", "username": "andi.pratama"},
    "34567890": {"nama": "Siti Rahayu", "username": "siti.rahayu"},
    "45678901": {"nama": "Reza Firmansyah", "username": "reza.firmansyah"},
    "56789012": {"nama": "Dewi Lestari", "username": "dewi.lestari"},
}

def resolve_member_details(nip: str) -> tuple[str, str]:
    nip_str = nip.strip()
    if nip_str in NIP_MAPPING:
        return NIP_MAPPING[nip_str]["nama"], NIP_MAPPING[nip_str]["username"]
    
    first_names = ["Aris", "Bambang", "Chandra", "Dedi", "Erwin", "Fajar", "Gita", "Hendra", "Indra", "Joko"]
    last_names = ["Kusuma", "Lestari", "Mulyadi", "Nasution", "Oktavian", "Prabowo", "Qadri", "Ramadhan", "Susanto", "Tanjung"]
    
    try:
        val = int(nip_str)
        fn_idx = val % len(first_names)
        ln_idx = (val // len(first_names)) % len(last_names)
        nama = f"{first_names[fn_idx]} {last_names[ln_idx]}"
        username = f"{first_names[fn_idx].lower()}.{last_names[ln_idx].lower()}"
    except ValueError:
        nama = f"Member {nip_str}"
        username = f"user_{nip_str}"
        
    return nama, username

AGENTS = {
    "Coordinator": {
        "name": "AI Coordinator",
        "domain": "Koordinasi project, blocker, reminder, rekomendasi, dan ringkasan lintas agent.",
        "icon": "🧠",
    },
    "IT": {
        "name": "Agent IT",
        "domain": "Scope teknis, architecture, flow job/program, dependency, dan runbook.",
        "icon": "⚙️",
    },
    "UAT": {
        "name": "Agent UAT",
        "domain": "Test scenario, defect log, readiness, evidence, dan test script.",
        "icon": "🧪",
    },
}
LABEL_WEIGHTS = {"Official": 1.0, "Draft": 0.75, "Informal": 0.5}


@dataclass
class Source:
    title: str
    label: str
    snippet: str
    score: float
    agent_scope: str


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def row_get(row: sqlite3.Row, key: str, default=None):
    """Safe get for sqlite3.Row objects which don't support .get()."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default



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
                notes text,
                knowledge_links text,
                active_agents text not null,
                created_at text not null
            );

            create table if not exists members (
                id integer primary key autoincrement,
                project_id text not null,
                nip text,
                nama text,
                username text not null,
                role text not null,
                unit text,
                created_at text not null
            );

            create table if not exists documents (
                id text primary key,
                project_id text not null,
                filename text not null,
                doc_type text not null default 'file',
                source_label text not null,
                agent_scope text not null,
                text text not null,
                ai_summary text,
                approval_status text not null default 'Approved',
                uploaded_by text,
                approved_by text,
                approved_at text,
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
        # Migrations for projects
        proj_cols = {row[1] for row in conn.execute("pragma table_info(projects)")}
        for col, stmt in {
            "notes": "alter table projects add column notes text",
            "knowledge_links": "alter table projects add column knowledge_links text",
        }.items():
            if col not in proj_cols:
                conn.execute(stmt)

        # Migrations for members
        mem_cols = {row[1] for row in conn.execute("pragma table_info(members)")}
        for col, stmt in {
            "nip": "alter table members add column nip text",
            "nama": "alter table members add column nama text",
            "unit": "alter table members add column unit text",
        }.items():
            if col not in mem_cols:
                conn.execute(stmt)

        # Migrations for documents
        doc_cols = {row[1] for row in conn.execute("pragma table_info(documents)")}
        for col, stmt in {
            "approval_status": "alter table documents add column approval_status text not null default 'Approved'",
            "uploaded_by": "alter table documents add column uploaded_by text",
            "approved_by": "alter table documents add column approved_by text",
            "approved_at": "alter table documents add column approved_at text",
            "doc_type": "alter table documents add column doc_type text not null default 'file'",
            "ai_summary": "alter table documents add column ai_summary text",
        }.items():
            if col not in doc_cols:
                conn.execute(stmt)

        conn.execute(
            """
            update documents
            set approval_status = 'Approved',
                approved_by = coalesce(approved_by, 'system'),
                approved_at = coalesce(approved_at, created_at)
            where approval_status is null or approval_status = ''
            """
        )
        conn.execute(
            """
            update documents
            set approved_by = coalesce(approved_by, 'system'),
                approved_at = coalesce(approved_at, created_at)
            where approval_status = 'Approved'
            """
        )
        conn.commit()


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


def create_project(name: str, description: str, release_id: str = "", change_id: str = "", notes: str = "", knowledge_links: list[dict] | None = None) -> str:
    project_id = f"{slugify(name)}-{stable_id(name, now_iso())[:6]}"
    active_agents = json.dumps(["Coordinator", "IT", "UAT"])
    links_json = json.dumps(knowledge_links or [])
    db_execute(
        """
        insert into projects (id, name, release_id, change_id, description, notes, knowledge_links, active_agents, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, name, release_id, change_id, description, notes, links_json, active_agents, now_iso()),
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
        "QR Payment Settlement Release",
        "Koordinasi release QR settlement untuk validasi transaksi, batch monitoring, dan readiness UAT.",
        "REL-2026-05",
        "CHG-88021",
        "Project ini mencakup perubahan validasi transaksi, update flow program settlement, dan penambahan monitoring batch.",
        [{"url": "https://confluence.bca.id/qr-settlement", "label": "Confluence - QR Settlement"}],
    )
    for nip, nama, username, role, unit in [
        ("12345678", "Budi Santoso", "cloverteam", "PO", "ITX IDS"),
        ("23456789", "Andi Pratama", "it.arch01", "IT", "ITX IDS"),
        ("34567890", "Siti Rahayu", "uat.lead02", "UAT", "UAT B"),
        ("45678901", "Reza Firmansyah", "ba.scope03", "BA", "SSI D"),
        ("56789012", "Dewi Lestari", "po.owner04", "PO", "SSI D"),
    ]:
        add_member(project_id, username, role, nip=nip, nama=nama, unit=unit)
    save_document(
        project_id,
        "BRD_scope_release.txt",
        "Official",
        "All",
        """
        Scope release mencakup perubahan validasi transaksi, update flow program settlement, dan penambahan monitoring batch.
        Dependency utama adalah approval change, kesiapan runbook rollback, dan sign-off UAT untuk scenario regression.
        """,
        doc_type="file",
        ai_summary="BRD mencakup 3 area utama: validasi transaksi, settlement flow, dan monitoring batch. Dependency kritis: approval change & UAT sign-off.",
        approval_status="Approved",
        uploaded_by="system",
        approved_by="system",
    )
    save_document(
        project_id,
        "Runbook_deployment.txt",
        "Official",
        "IT",
        """
        Deployment dilakukan setelah freeze window. Agent IT perlu mengecek dependency job nightly batch, database migration,
        smoke test service, dan rollback script. PIC teknis wajib validasi readiness sebelum release.
        """,
        doc_type="file",
        ai_summary="Runbook deployment: freeze window → cek dependency nightly batch → DB migration → smoke test → rollback script. PIC teknis wajib validasi.",
        approval_status="Approved",
        uploaded_by="system",
        approved_by="system",
    )
    save_document(
        project_id,
        "UAT_defect_log.csv",
        "Draft",
        "UAT",
        """
        scenario_id,status,severity,owner
        UAT-001,passed,low,uat.lead02
        UAT-002,open,high,it.arch01
        UAT-003,in progress,medium,uat.lead02
        Defect high masih open pada validasi limit transaksi dan perlu retest setelah patch.
        """,
        doc_type="file",
        ai_summary="3 test scenario: 1 passed, 1 open (severity high), 1 in progress. Defect kritis: validasi limit transaksi perlu retest post-patch.",
        approval_status="Approved",
        uploaded_by="system",
        approved_by="system",
    )
    store_chat(
        project_id,
        "Coordinator",
        "assistant",
        "Saya sudah membaca sumber yang tersedia. Blocker utama saat ini adalah defect high pada validasi limit transaksi dan konfirmasi rollback runbook sebelum freeze window.",
        [
            Source(
                "UAT_defect_log.csv",
                "Draft",
                "Defect high masih open pada validasi limit transaksi dan perlu retest setelah patch.",
                2.0,
                "UAT",
            ),
            Source(
                "Runbook_deployment.txt",
                "Official",
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


def add_member(project_id: str, username: str, role: str, nip: str = "", nama: str = "", unit: str = "") -> None:
    db_execute(
        "insert into members (project_id, nip, nama, username, role, unit, created_at) values (?, ?, ?, ?, ?, ?, ?)",
        (project_id, nip, nama, username, role, unit, now_iso()),
    )


def project_members(project_id: str) -> list[sqlite3.Row]:
    return db_rows("select * from members where project_id = ? order by role, username", (project_id,))


def project_docs(project_id: str, approved_only: bool = False) -> list[sqlite3.Row]:
    if approved_only:
        return db_rows(
            "select * from documents where project_id = ? and approval_status = 'Approved' order by created_at desc",
            (project_id,),
        )
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


def generate_ai_summary(filename: str, doc_type: str, text: str) -> str:
    """Generate a simple extractive summary from document text."""
    clean = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"[.!?\n]", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30][:4]
    if not sentences:
        return f"Dokumen {filename} berhasil diindeks. Konten tersedia untuk pencarian agent."
    summary = ". ".join(sentences[:3])
    if len(summary) > 300:
        summary = summary[:300] + "..."
    prefix = {"file": "📄", "note": "📝", "link": "🔗"}.get(doc_type, "📄")
    return f"{prefix} {summary}"


def save_document(
    project_id: str,
    filename: str,
    label: str,
    agent_scope: str,
    text: str,
    doc_type: str = "file",
    ai_summary: str | None = None,
    approval_status: str = "Approved",
    uploaded_by: str = "unknown",
    approved_by: str | None = None,
) -> None:
    doc_id = stable_id(project_id, filename, now_iso())
    path = UPLOAD_DIR / project_id
    path.mkdir(exist_ok=True)
    (path / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    approved_at = now_iso() if approval_status == "Approved" else None
    if ai_summary is None:
        ai_summary = generate_ai_summary(filename, doc_type, text)
    db_execute(
        """
        insert into documents (
            id, project_id, filename, doc_type, source_label, agent_scope, text, ai_summary,
            approval_status, uploaded_by, approved_by, approved_at, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, filename, doc_type, label, agent_scope, text, ai_summary, approval_status, uploaded_by, approved_by, approved_at, now_iso()),
    )


def approve_document(document_id: str, approved_by: str) -> None:
    db_execute(
        """
        update documents
        set approval_status = 'Approved', approved_by = ?, approved_at = ?
        where id = ?
        """,
        (approved_by, now_iso(), document_id),
    )


def can_approve_sources(project_id: str) -> bool:
    username = st.session_state.get("username", "")
    if username == LOGIN_USERNAME:
        return True
    roles = {member["role"] for member in project_members(project_id) if member["username"] == username}
    return bool(roles & {"PO"})


def current_project_roles(project_id: str) -> set[str]:
    username = st.session_state.get("username", "")
    if username == LOGIN_USERNAME:
        return {"PO"}
    return {member["role"] for member in project_members(project_id) if member["username"] == username}


def can_upload_sources(project_id: str) -> bool:
    roles = current_project_roles(project_id)
    return bool(roles)


def tokenize(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())}


def keyword_search(project_id: str, query: str, agent: str, limit: int = 5) -> list[Source]:
    q_words = tokenize(query)
    if not q_words:
        return []
    results: list[Source] = []
    for doc in project_docs(project_id, approved_only=True):
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
    if best.label == "Official" and best.score >= 2:
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
    return label


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
        answer += "\n\n⚠️ Perlu validasi oleh PIC terkait"
    return answer, sources, confidence


def coordinator_verbose_answer(project_id: str, question: str, sources: list[Source]) -> str:
    members = project_members(project_id)
    docs = project_docs(project_id, approved_only=True)
    pending_docs = [doc for doc in project_docs(project_id) if doc["approval_status"] == "Pending"]
    roles = pd.Series([member["role"] for member in members]).value_counts().to_dict() if members else {}
    labels = pd.Series([doc["source_label"] for doc in docs]).value_counts().to_dict() if docs else {}
    doc_signal = "\n".join(f"  - **{source.title}**: {source.snippet[:120]}" for source in sources[:4])
    if not doc_signal:
        doc_signal = "  - Belum ada sumber yang match langsung dengan pertanyaan ini."

    it_sources = keyword_search(project_id, question, "IT", limit=2)
    uat_sources = keyword_search(project_id, question, "UAT", limit=2)

    it_insight = it_sources[0].snippet[:150] if it_sources else "Tidak ada konteks teknis yang relevan saat ini."
    uat_insight = uat_sources[0].snippet[:150] if uat_sources else "Tidak ada konteks UAT yang relevan saat ini."

    blockers = blocker_signals(project_id)
    blocker_text = "\n".join(f"  - [{s['severity']}] {s['title']}" for s in blockers[:3]) if blockers else "  - Tidak ada blocker aktif."

    return textwrap.dedent(
        f"""
        🧠 **AI Coordinator** sedang memproses pertanyaan Anda...

        ---

        **⚙️ Agent IT** dihubungi:
        > {it_insight}

        **🧪 Agent UAT** dihubungi:
        > {uat_insight}

        ---

        **📊 Ringkasan Koordinasi:**

        - Komposisi tim: {roles or "belum ada member"}
        - Knowledge approved: **{len(docs)} dokumen** (distribusi: {labels or "belum ada"})
        - Knowledge pending: {len(pending_docs)} dokumen

        **Sinyal relevan dari knowledge base:**
        {doc_signal}

        **Blocker aktif:**
        {blocker_text}

        ---

        **✅ Rekomendasi AI Coordinator:**
        - Prioritaskan validasi sumber terpercaya sebelum keputusan release/change.
        - Jika ada gap dokumentasi IT atau UAT, assign PIC sesuai role sebelum meeting koordinasi.
        - Gunakan knowledge base yang sudah di-index untuk referensi objektif, bukan asumsi.
        """
    ).strip()


def coordinator_answer(project_id: str, question: str, sources: list[Source]) -> str:
    return coordinator_verbose_answer(project_id, question, sources)


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
    docs = project_docs(project_id, approved_only=True)
    members = project_members(project_id)
    doc_text = " ".join(doc["text"].lower() for doc in docs)
    has_it = any(doc["agent_scope"] in {"IT", "All"} for doc in docs)
    has_uat = any(doc["agent_scope"] in {"UAT", "All"} for doc in docs)
    has_official = any(doc["source_label"] == "Official" for doc in docs)
    defect_signal = any(term in doc_text for term in ["defect", "bug", "failed", "open"])
    return {
        "Knowledge": min(100, 25 + len(docs) * 15 + (20 if has_official else 0)),
        "Team": min(100, 30 + len(members) * 12 + (20 if {"IT", "UAT"} <= {m["role"] for m in members} else 0)),
        "IT Coverage": 85 if has_it else 35,
        "UAT Coverage": 85 if has_uat else 35,
        "Risk": 45 if defect_signal else 80,
    }


def approved_doc_text(project_id: str) -> str:
    return " ".join(doc["text"].lower() for doc in project_docs(project_id, approved_only=True))


def open_defect_count(project_id: str) -> int:
    text = approved_doc_text(project_id)
    return len(re.findall(r"\b(open|failed|blocker)\b", text))


def blocker_signals(project_id: str) -> list[dict[str, str]]:
    approved_docs = project_docs(project_id, approved_only=True)
    pending_docs = [doc for doc in project_docs(project_id) if doc["approval_status"] == "Pending"]
    text = " ".join(doc["text"].lower() for doc in approved_docs)
    signals: list[dict[str, str]] = []
    if re.search(r"\b(open|failed|blocker)\b", text) and "defect" in text:
        signals.append(
            {
                "severity": "High",
                "title": "Open defect needs disposition",
                "body": "Diambil dari approved UAT/defect source yang masih punya status open/failed/blocker.",
                "tone": "draft",
            }
        )
    if any(term in text for term in ["rollback", "runbook", "dependency", "nightly batch"]):
        signals.append(
            {
                "severity": "Medium",
                "title": "Technical readiness dependency",
                "body": "Ada sinyal runbook, rollback, atau dependency teknis yang perlu dikonfirmasi sebelum gate release.",
                "tone": "official",
            }
        )
    if pending_docs:
        signals.append(
            {
                "severity": "Medium",
                "title": f"{len(pending_docs)} source pending approval",
                "body": "Source pending belum dipakai agent untuk menjawab sampai PO approve.",
                "tone": "informal",
            }
        )
    scopes = {doc["agent_scope"] for doc in approved_docs}
    if not (scopes & {"IT", "All"}):
        signals.append({"severity": "Low", "title": "Missing approved IT source", "body": "Agent IT belum punya dokumen approved untuk menjawab detail teknis.", "tone": "informal"})
    if not (scopes & {"UAT", "All"}):
        signals.append({"severity": "Low", "title": "Missing approved UAT source", "body": "Agent UAT belum punya dokumen approved untuk readiness, defect, atau evidence.", "tone": "informal"})
    return signals[:5]


def milestone_progress(project_id: str) -> list[dict[str, object]]:
    docs = project_docs(project_id, approved_only=True)
    members = project_members(project_id)
    text = " ".join(doc["text"].lower() for doc in docs)
    has_it = any(doc["agent_scope"] in {"IT", "All"} for doc in docs)
    has_uat = any(doc["agent_scope"] in {"UAT", "All"} for doc in docs)
    has_official = any(doc["source_label"] == "Official" for doc in docs)
    has_it_member = any(member["role"] == "IT" for member in members)
    has_uat_member = any(member["role"] == "UAT" for member in members)
    has_runbook = any(term in text for term in ["runbook", "rollback", "deployment"])
    has_signoff = any(term in text for term in ["sign-off", "signoff", "approval"])
    defects = open_defect_count(project_id)
    return [
        {
            "name": "Scope freeze",
            "value": min(100, 35 + (35 if has_official else 0) + (30 if docs else 0)),
            "basis": "Official/approved scope source tersedia" if has_official else "Butuh approved scope/BRD untuk menjadi jelas",
        },
        {
            "name": "IT readiness",
            "value": min(100, 30 + (30 if has_it else 0) + (25 if has_runbook else 0) + (15 if has_it_member else 0)),
            "basis": "Dihitung dari approved IT source, runbook/rollback, dan member IT",
        },
        {
            "name": "UAT execution",
            "value": max(15, min(100, 25 + (35 if has_uat else 0) + (20 if has_uat_member else 0) + (20 if defects == 0 else 0))),
            "basis": "Dihitung dari approved UAT source, member UAT, dan defect terbuka",
        },
        {
            "name": "Release approval",
            "value": max(10, min(95, 20 + (25 if has_signoff else 0) + (20 if has_runbook else 0) + (20 if has_it and has_uat else 0) - min(defects * 8, 30))),
            "basis": "Gate naik jika sign-off/runbook ada; turun jika masih ada defect/blocker",
        },
    ]


def dashboard_metrics(project_id: str) -> dict[str, tuple[str, str]]:
    all_docs = project_docs(project_id)
    docs = project_docs(project_id, approved_only=True)
    release_date = datetime(2026, 5, 20).date()
    days_remaining = max((release_date - datetime.now().date()).days, 0)
    blockers = blocker_signals(project_id)
    defects = open_defect_count(project_id)
    milestones = milestone_progress(project_id)
    progress = round(sum(int(item["value"]) for item in milestones) / len(milestones)) if milestones else 0
    pending = len([doc for doc in all_docs if doc["approval_status"] == "Pending"])
    next_gate = "Approve sources" if pending else ("Resolve defect" if defects else "UAT sign-off")
    return {
        "Deadline": (release_date.strftime("%d %b %Y"), "Sample release date dari workspace"),
        "Remaining": (f"D-{days_remaining}", "Dihitung dari deadline"),
        "Progress": (f"{progress}%", "Rata-rata milestone berbasis evidence"),
        "Blockers": (str(len(blockers)), "Sinyal dari approved source + pending approval"),
        "Open Defects": (str(defects), "Keyword open/failed/blocker di approved defect source"),
        "Next Gate": (next_gate, "Gate berikutnya dari kondisi workspace"),
    }


def generate_faq(project_id: str) -> list[dict[str, str]]:
    """Generate FAQ from chat history and project context."""
    chats = db_rows("select * from chats where project_id = ? and role = 'user' order by created_at desc limit 10", (project_id,))
    docs = project_docs(project_id, approved_only=True)
    faqs = []
    # From chat history
    seen_q: set[str] = set()
    for chat in chats:
        q = chat["content"][:100]
        key = q[:40].lower()
        if key not in seen_q and len(q) > 10:
            seen_q.add(key)
            sources = keyword_search(project_id, chat["content"], "Coordinator", limit=1)
            answer = sources[0].snippet[:200] if sources else "Lihat knowledge base untuk detail lebih lanjut."
            faqs.append({"q": q, "a": answer})
    # If no faqs from chat, generate some from docs
    if not faqs and docs:
        for doc in docs[:3]:
            doc_type = row_get(doc, "doc_type") or "file"
            q = f"Apa ringkasan dari dokumen {doc['filename']}?"
            a = row_get(doc, "ai_summary") or doc["text"][:200]
            faqs.append({"q": q, "a": a})
            
    if not faqs:
        faqs.append({"q": "Belum ada pertanyaan terkait project ini?", "a": "Tim belum mengajukan pertanyaan ke AI Coordinator. Mulai diskusi di tab Chat untuk otomatis menghasilkan FAQ di sini berdasarkan histori koordinasi project."})
        
    return faqs[:5]


def inject_css() -> None:
    dark = st.session_state.get("dark_mode", False)
    app_bg = "#07111f" if dark else "#f0f4f9"
    sidebar_bg = "#0a1828" if dark else "#ffffff"
    card_bg = "#0f1c2e" if dark else "#ffffff"
    card_alt = "#13243a" if dark else "#f8fafd"
    text_color = "#edf4ff" if dark else "#111827"
    muted_color = "#7a94b0" if dark else "#6b7280"
    line_color = "#1e3050" if dark else "#e5eaf2"
    soft_blue = "#0f2747" if dark else "#eaf3ff"
    input_bg = "#0a1524" if dark else "#ffffff"
    section_color = "#7aa8d0" if dark else "#374151"
    brand_text = "#60a5fa" if dark else BCA_BLUE
    st.html(
        f"""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
        <style>
        :root {{
            --font: 'Plus Jakarta Sans', sans-serif !important;
            --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
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
            --bg: {app_bg};
        }}
        body, input, textarea, select, button, h1, h2, h3, h4, h5, h6, label {{
            font-family: 'Plus Jakarta Sans', sans-serif;
        }}
        .material-icons,
        .material-icons-outlined,
        .material-icons-round,
        .material-icons-sharp,
        .material-symbols-outlined,
        .material-symbols-rounded,
        .material-symbols-sharp,
        [data-testid="stIconMaterial"],
        [class*="material-symbols"],
        [class*="material-icons"] {{
            font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons', sans-serif !important;
        }}
        .block-container {{
            padding-top: 16px;
            padding-bottom: 28px;
            max-width: 1600px;
        }}
        [data-testid="stHeader"] {{
            background: transparent;
        }}
        .stApp {{
            background:
                radial-gradient(ellipse at 0% 0%, rgba(0,63,136,.12) 0%, transparent 50%),
                radial-gradient(ellipse at 100% 100%, rgba(0,166,214,.06) 0%, transparent 50%),
                {app_bg};
            color: var(--ink);
        }}
        [data-testid="stSidebar"] {{
            background: {sidebar_bg} !important;
            border-right: 1px solid var(--line);
        }}
        [data-testid="stSidebar"] * {{ color: var(--ink); }}
        [data-testid="stSidebar"] .stButton > button {{
            border-radius: 10px;
            border: 1px solid var(--line);
            background: transparent;
            color: var(--ink);
            min-height: 40px;
            font-weight: 600;
            transition: all .2s;
        }}
        [data-testid="stSidebar"] .stButton > button:hover {{
            border-color: var(--brand);
            background: var(--soft-blue);
            color: var(--brand);
        }}
        [data-baseweb="select"] > div {{
            border-radius: 10px;
            border-color: var(--line);
            background: var(--card-alt);
            color: var(--ink);
        }}
        [data-baseweb="select"] > div:hover {{ border-color: var(--brand) !important; }}
        [data-baseweb="select"] span {{ color: var(--ink); }}
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
        [data-baseweb="popover"] {{
            background: var(--card) !important;
            border: 1px solid var(--line) !important;
            border-radius: 10px !important;
        }}
        [data-baseweb="popover"] [role="option"] {{
            color: var(--ink) !important;
            background: var(--card) !important;
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
        .stCheckbox p {{ color: var(--ink) !important; }}
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
            border-radius: 10px !important;
            transition: border-color .2s;
        }}
        .stTextInput input:focus,
        .stTextArea textarea:focus {{
            border-color: var(--bca-blue) !important;
            box-shadow: 0 0 0 3px rgba(0,63,136,.12) !important;
        }}
        div[data-testid="stRadio"] label p,
        div[data-testid="stRadio"] label {{
            color: var(--ink) !important;
            font-weight: 500 !important;
        }}
        [data-testid="stFileUploader"] button {{
            background-color: var(--soft-blue) !important;
            color: var(--brand) !important;
            border: 1px solid rgba(0, 63, 136, 0.2) !important;
            border-radius: 8px !important;
            padding: 6px 16px !important;
            font-weight: 600 !important;
            transition: all 0.2s !important;
        }}
        [data-testid="stFileUploader"] button * {{
            color: var(--brand) !important;
        }}
        [data-testid="stFileUploader"] button:hover {{
            background-color: var(--brand) !important;
            border-color: var(--brand) !important;
        }}
        [data-testid="stFileUploader"] button:hover * {{
            color: #ffffff !important;
        }}
        h1, h2, h3 {{
            letter-spacing: -.01em;
            color: var(--ink);
        }}
        /* ─── Premium Split Login Page ─── */
        .stApp:has(.pm-login-split-trigger) {{
            background: radial-gradient(circle at 15% 50%, rgba(0, 166, 214, 0.12), transparent 50%),
                        radial-gradient(circle at 85% 30%, rgba(0, 63, 136, 0.15), transparent 50%),
                        var(--card-alt) !important;
        }}
        .stApp:has(.pm-login-split-trigger) .block-container {{
            max-width: 1100px !important;
            padding-top: 8vh !important;
        }}
        .stApp:has(.pm-login-split-trigger) [data-testid="stHeader"],
        .stApp:has(.pm-login-split-trigger) [data-testid="stSidebar"],
        .stApp:has(.pm-login-split-trigger) [data-testid="stSidebarCollapsedControl"] {{
            display: none !important;
        }}
        
        .pm-hero-title {{
            font-size: 3.8rem;
            font-weight: 900;
            line-height: 1.1;
            background: linear-gradient(135deg, var(--ink) 20%, var(--brand) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 20px;
            letter-spacing: -0.02em;
        }}
        .pm-hero-desc {{
            font-size: 1.15rem;
            line-height: 1.7;
            color: var(--muted);
            margin-bottom: 30px;
            max-width: 95%;
        }}
        .pm-feature-item {{
            display: flex;
            align-items: flex-start;
            gap: 16px;
            margin-bottom: 24px;
        }}
        .pm-feature-icon {{
            background: rgba(0, 166, 214, 0.1);
            color: var(--brand);
            width: 44px;
            height: 44px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            flex-shrink: 0;
            border: 1px solid rgba(0, 166, 214, 0.15);
        }}
        .pm-feature-text h4 {{
            margin: 0 0 4px 0;
            color: var(--ink);
            font-size: 1.05rem;
            font-weight: 700;
        }}
        .pm-feature-text p {{
            margin: 0;
            color: var(--muted);
            font-size: 0.9rem;
            line-height: 1.5;
        }}
        .pm-login-ai-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(0, 166, 214, 0.08);
            border: 1px solid rgba(0, 166, 214, 0.15);
            color: var(--brand) !important;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.1em;
            padding: 6px 14px;
            border-radius: 100px;
            text-transform: uppercase;
        }}
        .ai-badge-dot {{
            width: 8px;
            height: 8px;
            background-color: var(--brand);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--brand);
            animation: pm-pulse 2s infinite;
        }}
        @keyframes pm-pulse {{
            0% {{ opacity: 0.4; transform: scale(0.95); }}
            50% {{ opacity: 1; transform: scale(1.05); }}
            100% {{ opacity: 0.4; transform: scale(0.95); }}
        }}
        @keyframes pm-fade-in {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .stApp:has(.pm-login-split-trigger) div[data-testid="stForm"] {{
            background: var(--card) !important;
            border: 1px solid var(--line) !important;
            border-radius: 24px !important;
            padding: 40px !important;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.12) !important;
            position: relative;
            overflow: hidden;
            animation: pm-fade-in 0.8s ease-out forwards;
            animation-delay: 0.1s;
            opacity: 0;
        }}
        .stApp:has(.pm-login-split-trigger) div[data-testid="stForm"]::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 4px;
            background: linear-gradient(90deg, #00d2ff, #0052b4);
        }}
        .stApp:has(.pm-login-split-trigger) [data-testid="stWidgetLabel"] p {{
            font-size: 0.75rem !important;
            text-transform: uppercase !important;
            letter-spacing: 0.08em !important;
            font-weight: 700 !important;
            color: var(--muted) !important;
            margin-bottom: 4px !important;
        }}
        .stApp:has(.pm-login-split-trigger) .stTextInput input {{
            background-color: var(--input-bg) !important;
            border: 1px solid var(--line) !important;
            border-radius: 12px !important;
            height: 48px !important;
            padding: 0 16px !important;
            font-size: 0.95rem !important;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
        }}
        .stApp:has(.pm-login-split-trigger) .stTextInput input:focus {{
            border-color: var(--bca-blue) !important;
            box-shadow: 0 0 0 3px rgba(0, 63, 136, 0.15) !important;
        }}
        .stApp:has(.pm-login-split-trigger) div[data-testid="stForm"] button[type="submit"] {{
            background: linear-gradient(135deg, #0052b4 0%, #003F88 100%) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 12px !important;
            height: 50px !important;
            font-weight: 700 !important;
            font-size: 1rem !important;
            margin-top: 14px !important;
            box-shadow: 0 8px 20px rgba(0, 63, 136, 0.25) !important;
            transition: all 0.2s !important;
        }}
        .stApp:has(.pm-login-split-trigger) div[data-testid="stForm"] button[type="submit"]:hover {{
            transform: translateY(-2px) !important;
            box-shadow: 0 12px 24px rgba(0, 63, 136, 0.35) !important;
        }}
        .pm-login-creds-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background-color: var(--soft-blue);
            border: 1px solid rgba(0, 63, 136, 0.1);
            padding: 8px 16px;
            border-radius: 12px;
            font-size: 0.8rem;
            color: var(--brand);
            font-weight: 500;
        }}
        .pm-login-creds-value {{
            font-family: Menlo, Monaco, Consolas, monospace;
            background: rgba(0, 63, 136, 0.08);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.78rem;
            font-weight: 700;
        }}
        p, li, label, span {{ color: inherit; }}

        /* ─── Brand / Sidebar ─── */
        .pm-brand {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 4px 0 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--line);
        }}
        .pm-logo {{
            width: 38px;
            height: 38px;
            border-radius: 10px;
            display: grid;
            place-items: center;
            color: white;
            font-weight: 900;
            font-size: .9rem;
            background: linear-gradient(135deg, var(--bca-blue), var(--accent-cyan));
            box-shadow: 0 8px 20px rgba(0,63,136,.30);
            flex-shrink: 0;
        }}
        .pm-brand-title {{
            font-size: 1rem;
            font-weight: 800;
            line-height: 1.1;
            color: var(--ink);
        }}
        .pm-brand-subtitle {{
            color: var(--muted);
            font-size: .74rem;
            margin-top: 2px;
        }}

        /* ─── Sidebar Project Card ─── */
        .pm-project-info {{
            background: var(--soft-blue);
            border: 1px solid rgba(0,63,136,.15);
            border-radius: 10px;
            padding: 12px 14px;
            margin: 10px 0 16px;
        }}
        .pm-project-info-name {{
            font-weight: 800;
            font-size: .9rem;
            color: var(--brand);
            margin-bottom: 8px;
        }}
        .pm-project-stat {{
            display: flex;
            justify-content: space-between;
            font-size: .78rem;
            color: var(--muted);
            padding: 3px 0;
        }}
        .pm-project-stat strong {{
            color: var(--ink);
            font-weight: 700;
        }}

        /* ─── Top header strip ─── */
        .pm-topline {{
            border: 1px solid var(--line);
            background:
                linear-gradient(135deg, rgba(0,63,136,.08), transparent 42%),
                var(--card);
            padding: 20px 24px 18px;
            border-radius: 12px;
            margin-bottom: 16px;
            box-shadow: 0 4px 24px rgba(15,32,52,.06);
        }}
        .pm-kicker {{
            color: var(--brand);
            font-weight: 700;
            font-size: .74rem;
            text-transform: uppercase;
            letter-spacing: .06em;
        }}
        .pm-title-row {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: flex-start;
        }}
        .pm-title-row h1 {{
            font-size: 1.85rem;
            margin: 4px 0 2px;
            font-weight: 900;
        }}
        .pm-tagline {{
            color: var(--muted);
            margin-top: -2px;
            font-size: .9rem;
        }}
        .pm-header-metadata {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
            font-size: 0.84rem;
            color: var(--muted);
        }}
        .pm-meta-item {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}
        .pm-meta-item strong {{
            color: var(--ink);
            font-weight: 600;
        }}
        .pm-meta-divider {{
            color: var(--line);
            opacity: 0.6;
        }}
        .pm-header-description {{
            font-size: 0.88rem;
            color: var(--muted);
            margin-top: 12px;
            line-height: 1.5;
            max-width: 800px;
            border-top: 1px solid var(--line);
            padding-top: 12px;
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
            padding: 4px 10px;
            border-radius: 999px;
            background: var(--soft-blue);
            border: 1px solid rgba(0,63,136,.16);
            color: var(--brand);
            font-size: .75rem;
            font-weight: 700;
            white-space: nowrap;
        }}
        .pm-live {{
            border: 1px solid rgba(32,167,123,.25);
            background: rgba(32,167,123,.10);
            color: var(--accent-green);
        }}

        /* ─── Cards / Shell ─── */
        .pm-shell {{
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--card);
            padding: 18px;
            box-shadow: 0 4px 20px rgba(15,32,52,.05);
            transition: box-shadow .2s;
        }}
        .pm-shell:hover {{
            box-shadow: 0 8px 32px rgba(15,32,52,.08);
        }}
        .pm-section-title {{
            font-size: .72rem;
            font-weight: 800;
            letter-spacing: .06em;
            text-transform: uppercase;
            color: var(--section);
            margin-bottom: 10px;
        }}

        /* ─── Metric/Readiness ─── */
        .pm-readiness-grid {{
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 10px;
            margin: 12px 0 18px;
        }}
        .pm-readiness {{
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--card);
            padding: 16px;
            transition: transform .2s;
        }}
        .pm-readiness:hover {{
            transform: translateY(-2px);
        }}
        .pm-readiness-label {{
            color: var(--muted);
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .05em;
        }}
        .pm-readiness-value {{
            font-size: 1.7rem;
            font-weight: 900;
            margin-top: 4px;
            color: var(--ink);
        }}
        .pm-dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin: 10px 0 20px;
        }}
        .pm-dashboard-card {{
            border: 1px solid var(--line);
            border-radius: 12px;
            background: var(--card);
            padding: 16px;
            min-height: 110px;
            transition: transform .2s;
        }}
        .pm-dashboard-card:hover {{
            transform: translateY(-2px);
        }}
        .pm-dashboard-label {{
            color: var(--section);
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .05em;
        }}
        .pm-dashboard-value {{
            color: var(--ink);
            font-size: 1.45rem;
            font-weight: 900;
            margin-top: 8px;
            line-height: 1.1;
            overflow-wrap: anywhere;
        }}
        .pm-dashboard-hint {{
            color: var(--muted);
            font-size: .74rem;
            line-height: 1.35;
            margin-top: 8px;
        }}
        .pm-bar {{
            height: 6px;
            background: rgba(102,112,133,.18);
            border-radius: 99px;
            overflow: hidden;
            margin-top: 10px;
        }}
        .pm-fill {{ height: 100%; border-radius: 99px; }}
        .pm-fill.green {{ background: linear-gradient(90deg, var(--accent-green), #16c784); }}
        .pm-fill.amber {{ background: linear-gradient(90deg, var(--accent-amber), #f7b731); }}
        .pm-fill.red {{ background: linear-gradient(90deg, #E55353, #ff6b6b); }}

        /* ─── Progress Lines ─── */
        .pm-progress-line {{
            display: grid;
            grid-template-columns: 140px 1fr 52px;
            gap: 12px;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid var(--line);
        }}
        .pm-progress-line:last-child {{ border-bottom: 0; }}
        .pm-progress-name {{ color: var(--ink); font-weight: 700; font-size: .9rem; }}
        .pm-progress-basis {{
            grid-column: 2 / 4;
            color: var(--muted);
            font-size: .76rem;
            line-height: 1.35;
            margin-top: -4px;
        }}

        /* ─── Sources / Pills ─── */
        .pm-source {{
            border-left: 3px solid var(--bca-blue);
            padding: 10px 14px;
            background: var(--card-alt);
            border-radius: 0 8px 8px 0;
            margin-bottom: 10px;
            font-size: .88rem;
            transition: border-color .2s;
        }}
        .pm-source:hover {{ border-left-color: var(--accent-cyan); }}
        .pm-source-title {{ font-weight: 700; color: var(--ink); margin: 5px 0 4px; word-break: break-word; overflow-wrap: break-word; }}
        .pm-source-body {{ color: var(--muted); line-height: 1.45; font-size: .84rem; word-break: break-word; overflow-wrap: break-word; }}
        .pm-pill {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background: rgba(0,63,136,.08);
            color: var(--brand);
            font-size: .72rem;
            font-weight: 700;
        }}
        .pm-pill.official {{ background: rgba(245,166,35,.12); color: #c07a00; }}
        .pm-pill.draft {{ background: rgba(0,166,214,.12); color: var(--accent-cyan); }}
        .pm-pill.informal {{ background: rgba(102,112,133,.12); color: var(--muted); }}
        .pm-pill.note {{ background: rgba(32,167,123,.12); color: var(--accent-green); }}
        .pm-pill.link {{ background: rgba(245,166,35,.12); color: var(--accent-amber); }}

        /* ─── Action items ─── */
        .pm-action {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--line);
        }}
        .pm-action:last-child {{ border-bottom: 0; }}
        .pm-action strong {{ color: var(--ink); font-weight: 700; }}
        .pm-action span {{ color: var(--muted); font-size: .84rem; }}
        .pm-severity-high {{ color: #E55353; font-weight: 700; font-size: .78rem; }}
        .pm-severity-medium {{ color: var(--accent-amber); font-weight: 700; font-size: .78rem; }}
        .pm-severity-low {{ color: var(--muted); font-weight: 700; font-size: .78rem; }}

        /* ─── Knowledge AI Summary ─── */
        .pm-ai-summary {{
            border: 1px solid rgba(0,63,136,.18);
            background: linear-gradient(135deg, rgba(0,63,136,.06), transparent);
            border-radius: 10px;
            padding: 14px;
            margin-bottom: 12px;
        }}
        .pm-ai-summary-label {{
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .05em;
            color: var(--brand);
            margin-bottom: 6px;
        }}
        .pm-ai-summary-text {{
            color: var(--ink);
            font-size: .86rem;
            line-height: 1.5;
        }}
        .pm-context-card {{
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--card-alt);
            padding: 14px;
            margin-bottom: 12px;
        }}
        .pm-context-label {{
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .05em;
            color: var(--section);
            margin-bottom: 8px;
        }}

        /* ─── FAQ ─── */
        .pm-faq-item {{
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--card);
            padding: 14px 16px;
            margin-bottom: 10px;
            transition: border-color .2s;
        }}
        .pm-faq-item:hover {{ border-color: var(--brand); }}
        .pm-faq-q {{
            font-weight: 700;
            color: var(--ink);
            font-size: .9rem;
            margin-bottom: 6px;
        }}
        .pm-faq-a {{
            color: var(--muted);
            font-size: .85rem;
            line-height: 1.5;
        }}

        /* ─── Members Table ─── */
        .pm-member-row {{
            display: grid;
            grid-template-columns: 120px 1fr 100px 120px;
            gap: 10px;
            padding: 10px 12px;
            border-bottom: 1px solid var(--line);
            align-items: center;
        }}
        .pm-member-header {{
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            color: var(--section);
        }}
        .pm-member-nip {{ font-family: monospace; font-size: .85rem; color: var(--muted); }}
        .pm-member-name {{ font-weight: 700; color: var(--ink); }}

        /* ─── Dashboard Sections ─── */
        .pm-section-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 14px;
        }}
        .pm-section-icon {{
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: grid;
            place-items: center;
            font-size: 1rem;
            background: var(--soft-blue);
        }}
        .pm-section-heading {{
            font-size: 1rem;
            font-weight: 800;
            color: var(--ink);
            margin: 0;
        }}
        .pm-recommendation {{
            background: linear-gradient(135deg, rgba(32,167,123,.08), transparent);
            border: 1px solid rgba(32,167,123,.20);
            border-radius: 10px;
            padding: 12px 14px;
            margin-bottom: 10px;
        }}
        .pm-recommendation-text {{
            color: var(--ink);
            font-size: .88rem;
            line-height: 1.5;
        }}
        .pm-overview-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 14px;
        }}
        .pm-overview-item {{
            background: var(--card-alt);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 12px 14px;
        }}
        .pm-overview-label {{
            font-size: .72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .05em;
            color: var(--section);
            margin-bottom: 4px;
        }}
        .pm-overview-val {{
            font-size: .92rem;
            font-weight: 700;
            color: var(--ink);
        }}

        /* ─── Chat ─── */
        .pm-coordinator-banner {{
            display: flex;
            align-items: center;
            gap: 12px;
            background: linear-gradient(135deg, rgba(0,63,136,.10), rgba(0,166,214,.06));
            border: 1px solid rgba(0,63,136,.20);
            border-radius: 12px;
            padding: 14px 18px;
            margin-bottom: 16px;
        }}
        .pm-coordinator-icon {{
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, var(--bca-blue), var(--accent-cyan));
            border-radius: 12px;
            display: grid;
            place-items: center;
            font-size: 1.3rem;
            flex-shrink: 0;
        }}
        .pm-coordinator-name {{
            font-weight: 800;
            font-size: 1rem;
            color: var(--ink);
        }}
        .pm-coordinator-desc {{
            font-size: .83rem;
            color: var(--muted);
            margin-top: 2px;
        }}
        /* ─── Chat Messages — scrollable area ─── */
        div[data-testid="stChatMessage"] {{
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--card);
            padding: 8px 12px;
            margin-bottom: 6px;
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
            /* Tidak pasang word-break di sini agar konten overflow bisa scroll */
        }}
        /* Teks biasa (paragraf & list) — warna sesuai mode, biarkan browser wrap secara natural */
        div[data-testid="stChatMessage"] p,
        div[data-testid="stChatMessage"] li,
        div[data-testid="stChatMessage"] span,
        div[data-testid="stChatMessage"] strong,
        div[data-testid="stChatMessage"] em,
        div[data-testid="stChatMessage"] b,
        div[data-testid="stChatMessage"] i {{
            color: var(--ink) !important;
        }}
        /* Heading juga wrap, warna sesuai mode */
        div[data-testid="stChatMessage"] h1,
        div[data-testid="stChatMessage"] h2,
        div[data-testid="stChatMessage"] h3,
        div[data-testid="stChatMessage"] h4,
        div[data-testid="stChatMessage"] h5,
        div[data-testid="stChatMessage"] h6 {{
            color: var(--ink) !important;
            overflow-wrap: break-word;
        }}
        /* div/container dalam chat — warna saja, jangan paksa word-break */
        div[data-testid="stChatMessage"] div {{
            color: var(--ink) !important;
        }}
        /* code inline — wrap tapi font monospace */
        div[data-testid="stChatMessage"] code {{
            color: var(--ink) !important;
            font-family: var(--font-mono) !important;
            font-size: 0.85em !important;
            background: var(--card-alt) !important;
            padding: 1px 5px !important;
            border-radius: 4px !important;
            overflow-wrap: break-word;
        }}
        /* pre/code block — JANGAN word-break, biarkan scroll horizontal */
        div[data-testid="stChatMessage"] pre {{
            overflow-x: auto !important;
            white-space: pre !important;  /* no wrap — scroll horizontal */
            font-family: var(--font-mono) !important;
            font-size: 0.85em !important;
            padding: 10px 12px !important;
            border-radius: 6px !important;
            background: var(--card-alt) !important;
            color: var(--ink) !important;
            max-width: 100% !important;
        }}
        div[data-testid="stChatMessage"] small {{ color: var(--muted) !important; }}
        /* Kolom yang mengandung chat — scroll horizontal, min-width fix */
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stChatMessage"]) > div[data-testid="stColumn"] {{
            min-width: 0;
            overflow-x: auto;
        }}
        /* Setiap chat message: bisa scroll ke kanan jika isinya panjang */
        div[data-testid="stChatMessage"] {{
            overflow-x: auto;
        }}
        div[data-testid="stColumn"] {{
            min-width: 0;
        }}
        /* blockquote — bisa scroll horizontal, jangan dipaksa wrap */
        blockquote {{
            background: var(--card-alt) !important;
            border-left: 4px solid var(--brand) !important;
            color: var(--ink) !important;
            padding: 10px 14px !important;
            border-radius: 6px !important;
            margin: 10px 0 !important;
            overflow-x: auto !important;
        }}
        /* pre/code global */
        pre, code, [data-testid="stMarkdownContainer"] pre, [data-testid="stMarkdownContainer"] code {{
            background-color: var(--card-alt) !important;
            color: var(--ink) !important;
            font-family: var(--font-mono) !important;
        }}
        [data-testid="stMarkdownContainer"] pre {{
            overflow-x: auto !important;
            white-space: pre !important;
        }}

        /* ─── Sticky Chat Input ─── */
        /* Streamlit menaruh stBottom sebagai fixed bottom — kita pastikan:
           1. Tinggi konten chat bisa discroll (block-container punya padding bawah)
           2. Background input kontras sesuai mode */
        section.main > div.block-container {{
            padding-bottom: 120px !important;
        }}
        /* Container sticky dari Streamlit */
        div[data-testid="stBottom"] {{
            position: fixed !important;
            bottom: 0 !important;
            left: 0 !important;
            right: 0 !important;
            z-index: 999999 !important;
            background: var(--bg) !important;
            backdrop-filter: blur(16px) !important;
            -webkit-backdrop-filter: blur(16px) !important;
            border-top: 1px solid var(--line) !important;
            box-shadow: 0 -4px 24px rgba(0,0,0,0.10) !important;
            padding: 10px 0 12px !important;
        }}
        div[data-testid="stChatInput"] {{
            background: transparent !important;
            background-color: transparent !important;
            padding: 0 !important;
            border: none !important;
        }}
        div[data-testid="stChatInput"] > div,
        div[data-testid="stChatInput"] [data-testid="stChatInputContainer"] {{
            background: var(--card) !important;
            background-color: var(--card) !important;
            border: 1.5px solid var(--line) !important;
            border-radius: 14px !important;
            box-shadow: 0 2px 16px rgba(0,0,0,0.10) !important;
            transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
        }}
        div[data-testid="stChatInput"] > div:focus-within,
        div[data-testid="stChatInput"] [data-testid="stChatInputContainer"]:focus-within {{
            border-color: var(--brand) !important;
            box-shadow: 0 0 0 3px rgba(0,63,136,0.12), 0 4px 20px rgba(0,0,0,0.10) !important;
        }}
        /* Paksa semua inner div dan textarea menjadi transparan agar background kartu luar (putih/gelap) terlihat */
        div[data-testid="stChatInput"] > div *:not(button),
        div[data-testid="stChatInput"] [data-testid="stChatInputContainer"] *:not(button) {{
            background: transparent !important;
            background-color: transparent !important;
        }}
        div[data-testid="stChatInput"] textarea {{
            color: var(--ink) !important;
            border: none !important;
            padding: 12px 14px !important;
            font-family: 'Plus Jakarta Sans', sans-serif !important;
            font-size: 0.95rem !important;
            resize: none !important;
            box-shadow: none !important;
        }}
        div[data-testid="stChatInput"] textarea::placeholder {{
            color: var(--muted) !important;
        }}
        div[data-testid="stChatInput"] button {{
            background: linear-gradient(135deg, var(--bca-blue), var(--accent-cyan)) !important;
            color: white !important;
            border: none !important;
            border-radius: 10px !important;
            width: 36px !important;
            height: 36px !important;
            display: grid !important;
            place-items: center !important;
            transition: transform 0.15s ease, box-shadow 0.15s ease !important;
            margin: 4px !important;
            box-shadow: 0 2px 8px rgba(0,63,136,0.25) !important;
        }}
        div[data-testid="stChatInput"] button:hover {{
            transform: scale(1.08) !important;
            box-shadow: 0 4px 14px rgba(0,63,136,0.40) !important;
        }}

        /* ─── File Uploader ─── */
        [data-testid="stFileUploader"] label,
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] p {{ color: var(--ink) !important; }}
        [data-testid="stFileUploaderDropzone"] {{
            background: var(--card-alt) !important;
            border: 2px dashed var(--line) !important;
            border-radius: 10px !important;
            transition: all .2s;
        }}
        [data-testid="stFileUploaderDropzone"]:hover {{
            border-color: var(--brand) !important;
            background: var(--soft-blue) !important;
        }}

        /* ─── Buttons ─── */
        .stButton > button[kind="primary"], .stFormSubmitButton > button {{
            background: linear-gradient(135deg, var(--bca-blue), #0055b8);
            border: none;
            border-radius: 10px;
            color: #ffffff;
            font-weight: 700;
            transition: all .2s;
            box-shadow: 0 4px 14px rgba(0,63,136,.30);
        }}
        .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(0,63,136,.40);
        }}
        .stButton > button {{
            border-radius: 10px;
            border: 1px solid var(--line);
            background: var(--card);
            color: var(--ink);
            font-weight: 600;
            transition: all .2s;
        }}
        .stButton > button:hover {{
            border-color: var(--brand);
            color: var(--brand);
            background: var(--soft-blue);
        }}

        /* ─── Tabs ─── */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            border-bottom: 1px solid var(--line);
            background: transparent;
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 8px 8px 0 0;
            padding: 10px 18px;
            color: var(--muted);
            font-weight: 600;
            font-size: .88rem;
        }}
        .stTabs [aria-selected="true"] {{
            background: var(--card);
            color: var(--brand);
            border: 1px solid var(--line);
            border-bottom-color: var(--card);
            font-weight: 700;
        }}
        .stTabs [data-baseweb="tab-highlight"] {{
            background-color: var(--bca-blue);
        }}

        /* ─── Metrics ─── */
        div[data-testid="stMetric"] {{
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 12px 14px;
        }}
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] div {{ color: var(--brand) !important; }}
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] p {{
            color: var(--section) !important;
            font-weight: 700 !important;
        }}

        /* ─── Governance note ─── */
        .pm-governance {{
            border: 1px solid var(--line);
            background: var(--card-alt);
            border-radius: 10px;
            padding: 12px 14px;
            color: var(--muted);
            line-height: 1.5;
            font-size: .86rem;
            margin-bottom: 14px;
        }}

        /* ─── Userbar ─── */
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
            padding: 5px 12px;
            font-weight: 700;
            font-size: .83rem;
        }}

        /* ─── Dataframe ─── */
        .stDataFrame, [data-testid="stDataFrame"] {{
            border: 1px solid var(--line);
            border-radius: 10px;
            overflow: hidden;
        }}

        /* ─── Knowledge form tabs ─── */
        .pm-intake-tab {{
            display: flex;
            gap: 6px;
            margin-bottom: 16px;
        }}
        .pm-intake-btn {{
            padding: 6px 14px;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: var(--card-alt);
            color: var(--muted);
            font-size: .82rem;
            font-weight: 600;
            cursor: pointer;
        }}
        .pm-intake-btn.active {{
            background: var(--bca-blue);
            border-color: var(--bca-blue);
            color: white;
        }}

        @media (max-width: 1024px) {{
            .pm-readiness-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
            .pm-dashboard-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
            .pm-title-row {{ display: block; }}
            .pm-overview-grid {{ grid-template-columns: 1fr; }}
        }}
        @media (max-width: 768px) {{
            .pm-login-hero {{ display: none; }}
            .pm-readiness-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        }}
        </style>
        """
    )


def render_header(project: sqlite3.Row | None) -> None:
    title = project["name"] if project else "ProjectMind"
    release_id = project["release_id"] if project and project["release_id"] else None
    change_id = project["change_id"] if project and project["change_id"] else None
    
    chips = ""
    if release_id:
        chips += f'<span class="pm-chip">📋 {esc(release_id)}</span>'
    if change_id:
        chips += f'<span class="pm-chip">🔄 {esc(change_id)}</span>'
    chips += '<span class="pm-chip pm-live">● Live</span>'
    
    if project:
        p_id = project["id"]
        members_count = len(project_members(p_id))
        docs_count = len(project_docs(p_id))
        links_count = len(json.loads(row_get(project, "knowledge_links") or "[]"))
        
        metadata_html = (
            f'<div class="pm-header-metadata">'
            f'<span class="pm-meta-item">📄 <strong>{docs_count}</strong> Documents</span>'
            f'<span class="pm-meta-divider">·</span>'
            f'<span class="pm-meta-item">🔗 <strong>{links_count}</strong> Links</span>'
            f'</div>'
        )
        desc_html = f'<div class="pm-header-description">{esc(project["description"])}</div>' if project["description"] else ''
    else:
        metadata_html = (
            '<div class="pm-header-metadata">'
            '<span class="pm-meta-item">✨ Create a brand new project workspace powered by Agentic AI coordinator</span>'
            '</div>'
        )
        desc_html = ""
        
    st.markdown(
        f'<div class="pm-topline">'
        f'<div class="pm-title-row">'
        f'<div>'
        f'<div class="pm-kicker">Project Workspace</div>'
        f'<h1>{esc(title)}</h1>'
        f'{metadata_html}'
        f'</div>'
        f'<div class="pm-chip-row">{chips}</div>'
        f'</div>'
        f'{desc_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_login() -> None:
    st.markdown('<div class="pm-login-split-trigger"></div>', unsafe_allow_html=True)
    
    left, right = st.columns([1.3, 1], gap="large")
    
    with left:
        st.markdown(
            """
<div style="padding-right: 30px; animation: pm-fade-in 0.8s ease-out forwards;">
<div class="pm-login-ai-badge" style="margin-bottom: 24px;">
<span class="ai-badge-dot"></span> Agentic Workflow Workspace
</div>
<h1 class="pm-hero-title">ProjectMind</h1>
<div class="pm-feature-item">
<div class="pm-feature-icon">📂</div>
<div class="pm-feature-text">
<h4>Knowledge Base Terpusat</h4>
<p>Upload requirement, Runbook, atau Defect Log. AI akan mengekstrak dependensi dan konteks project secara otomatis.</p>
</div>
</div>
<div class="pm-feature-item">
<div class="pm-feature-icon">🤖</div>
<div class="pm-feature-text">
<h4>AI Coordinator & Agents</h4>
<p>Agent IT dan UAT berjalan di latar belakang untuk menganalisis project overview, summary secara real-time.</p>
</div>
</div>
</div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)
        with st.form("login_form"):
            st.markdown(
                """
                <h2 style="font-size: 1.6rem; font-weight: 800; margin-bottom: 24px; color: var(--ink); text-align: center;">Akses Workspace</h2>
                """,
                unsafe_allow_html=True,
            )
            username = st.text_input("Username", placeholder="Masukkan username Anda")
            password = st.text_input("Password", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Masuk ke Workspace", use_container_width=True)
            
            st.markdown(
                f"""
                <div style="text-align: center; margin-top: 20px; font-size: 0.85rem; color: var(--muted);">
                    🔑 Demo: <strong>cloverteam</strong> / <strong>bic2026</strong>
                </div>
                """,
                unsafe_allow_html=True,
            )
            
        if submitted:
            if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                st.rerun()
            else:
                st.error("❌ Username atau password tidak sesuai. Coba lagi.")


def render_project_creator() -> str | None:
    st.markdown(
        """
        <div class="pm-section-header">
            <div class="pm-section-icon">🚀</div>
            <div>
                <div class="pm-section-heading">Buat Project Workspace Baru</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_main, col_side = st.columns([1.4, 1], gap="large")
    with col_main:
        with st.form("create_project"):
            # ── Required
            st.markdown('<div class="pm-section-title">Informasi Project (Wajib)</div>', unsafe_allow_html=True)
            name = st.text_input(
                "Nama Project ✱",
                placeholder="Contoh: QR Payment Settlement Release",
            )
            description = st.text_area(
                "Deskripsi Project ✱",
                placeholder="Jelaskan scope, background, tujuan utama, dan konteks koordinasi project ini...",
                height=120,
            )

            st.divider()

            # ── Optional Admin
            st.markdown('<div class="pm-section-title">Informasi Administrasi (Opsional — bisa diisi nanti)</div>', unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            change_id = col1.text_input("Change ID", placeholder="CHG-88021")
            release_id = col2.text_input("Release ID", placeholder="REL-2026-05")
            notes = st.text_area(
                "Notes Project",
                placeholder="Catatan koordinasi, dependency penting, atau hal yang perlu diingat tim...",
                height=80,
            )

            st.divider()

            # ── Optional Knowledge Links
            st.markdown('<div class="pm-section-title">Knowledge Links (Opsional — link informasi penting)</div>', unsafe_allow_html=True)
            link1_col, label1_col = st.columns([2, 1])
            link1 = link1_col.text_input("URL #1", placeholder="https://confluence.bca.id/...")
            label1 = label1_col.text_input("Label #1", placeholder="Confluence - BRD")
            link2_col, label2_col = st.columns([2, 1])
            link2 = link2_col.text_input("URL #2", placeholder="https://jira.bca.id/...")
            label2 = label2_col.text_input("Label #2", placeholder="Jira - Sprint Board")

            submitted = st.form_submit_button("✨ Buat Project Workspace", use_container_width=True, type="primary")

        if submitted:
            if not name.strip():
                st.warning("⚠️ Nama Project wajib diisi.")
                return None
            if not description.strip():
                st.warning("⚠️ Deskripsi Project wajib diisi.")
                return None
            knowledge_links = []
            if link1.strip():
                knowledge_links.append({"url": link1.strip(), "label": label1.strip() or link1.strip()})
            if link2.strip():
                knowledge_links.append({"url": link2.strip(), "label": label2.strip() or link2.strip()})
            project_id = create_project(
                name.strip(),
                description.strip(),
                release_id.strip(),
                change_id.strip(),
                notes.strip(),
                knowledge_links,
            )
            creator_uname = st.session_state.get("username", LOGIN_USERNAME)
            if creator_uname == "cloverteam":
                add_member(project_id, creator_uname, "PO", nip="12345678", nama="Budi Santoso", unit="ITX IDS")
            else:
                add_member(project_id, creator_uname, "PO", nip="99999999", nama="Project Owner", unit="SSI D")
            return project_id

    with col_side:
        st.markdown(
            """
            <div class="pm-context-card">
                <div class="pm-context-label">💡 Panduan Pengisian</div>
                <div style="color: var(--muted); font-size: .84rem; line-height: 1.6;">
                    <strong style="color: var(--ink);">Wajib diisi:</strong><br/>
                    • <strong>Nama Project</strong> — nama yang jelas dan deskriptif<br/>
                    • <strong>Deskripsi</strong> — scope, background, dan tujuan project<br/><br/>
                    <strong style="color: var(--ink);">Bisa diisi nanti:</strong><br/>
                    • Change ID & Release ID<br/>
                    • Notes koordinasi tambahan<br/>
                    • Link ke Confluence, Jira, dll.<br/><br/>
                    <strong style="color: var(--ink);">Otomatis aktif:</strong><br/>
                    • AI Coordinator (otak workspace)<br/>
                    • Agent IT (scope teknis)<br/>
                    • Agent UAT (readiness & defect)
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
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
            format_func=lambda pid: next(item["name"] for item in items if item["id"] == pid),
            label_visibility="collapsed",
        )
        st.session_state["project_id"] = selected_id
        selected_project = next(item for item in items if item["id"] == selected_id)

        members = project_members(selected_id)
        role_dist = {}
        for m in members:
            role_dist[m["role"]] = role_dist.get(m["role"], 0) + 1
        active_roles = ", ".join(role_dist.keys()) if role_dist else "None"
        
        proj_links_raw = selected_project["knowledge_links"] or "[]"
        try:
            proj_links = json.loads(proj_links_raw)
        except Exception:
            proj_links = []

        st.sidebar.markdown(
            f"""
            <div class="pm-project-info">
                <div class="pm-project-info-name">📁 {esc(selected_project['name'])}</div>
                <div class="pm-project-stat"><span>Tim</span><strong>{len(members)} Members</strong></div>
                <div class="pm-project-stat"><span>Role Aktif</span><strong>{esc(active_roles)}</strong></div>
                <div class="pm-project-stat"><span>AI Coordinator</span><strong>🟢 Online</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Knowledge links in sidebar
        if proj_links:
            st.sidebar.markdown('<div class="pm-section-title" style="margin-top: 4px;">🔗 Knowledge Links</div>', unsafe_allow_html=True)
            for link_item in proj_links[:4]:
                st.sidebar.markdown(
                    f'<div style="font-size:.8rem; padding: 4px 0; border-bottom: 1px solid var(--line);"><a href="{esc(link_item["url"])}" target="_blank" style="color: var(--brand); text-decoration: none;">↗ {esc(link_item["label"])}</a></div>',
                    unsafe_allow_html=True,
                )

    elif not items:
        st.sidebar.info("Belum ada workspace. Buat project pertama Anda!")
    else:
        st.sidebar.caption("Mode create aktif.")

    st.sidebar.divider()
    if st.sidebar.button("＋ New Project Workspace", use_container_width=True):
        st.session_state["show_create"] = True
        st.rerun()
    if create_mode and st.sidebar.button("← Kembali ke Workspace", use_container_width=True):
        st.session_state["show_create"] = False
        st.rerun()

    st.sidebar.divider()
    # User info & controls
    username = st.session_state.get("username", "")
    st.sidebar.markdown(
        f'<div style="font-size:.78rem; color: var(--muted);">Masuk sebagai</div><div style="font-size:.88rem; font-weight: 700; color: var(--ink); margin-bottom: 12px;">👤 {esc(username)}</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.toggle("Dark mode", key="dark_mode")
    if st.sidebar.button("Logout", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()

    return selected_project


def render_top_controls() -> None:
    pass


def render_members(project_id: str) -> None:
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">👥</div>
                <div class="pm-section-heading">Project Members</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        members = project_members(project_id)
        if members:
            # Header row
            st.markdown(
                """
                <div class="pm-member-row">
                    <div class="pm-member-header">NIP</div>
                    <div class="pm-member-header">Nama Lengkap</div>
                    <div class="pm-member-header">Role</div>
                    <div class="pm-member-header">Unit / Biro</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            role_colors = {"IT": "#0078d4", "UAT": "#20A77B", "BA": "#F5A623", "PO": "#8b5cf6"}
            for m in members:
                nip = m["nip"] or "-"
                nama = m["nama"] or m["username"]
                unit = m["unit"] or "-"
                role = m["role"]
                rc = role_colors.get(role, "#6b7280")
                st.markdown(
                    f"""
                    <div class="pm-member-row">
                        <div class="pm-member-nip">{esc(nip)}</div>
                        <div class="pm-member-name">{esc(nama)}</div>
                        <div><span style="background:{rc}18; color:{rc}; font-size:.74rem; font-weight:700; padding:2px 8px; border-radius:999px;">{esc(role)}</span></div>
                        <div style="font-size:.83rem; color: var(--muted);">{esc(unit)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("Belum ada member di workspace ini. Tambahkan member pertama!")

    with right:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">➕</div>
                <div class="pm-section-heading">Tambah Member</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="pm-governance">PO dapat mengundang member baru. Member mendapat akses sesuai role yang diberikan.</div>',
            unsafe_allow_html=True,
        )
        if can_approve_sources(project_id):
            with st.form("add_member", clear_on_submit=True):
                nip = st.text_input("NIP ✱", placeholder="Contoh: 12345678")
                col_role, col_unit = st.columns(2)
                role = col_role.selectbox("Role", ROLES)
                unit = col_unit.selectbox("Unit / Biro", UNITS)
                submitted = st.form_submit_button("Tambah Member", use_container_width=True, type="primary")
            if submitted:
                nip_clean = nip.strip()
                if not nip_clean:
                    st.warning("⚠️ NIP wajib diisi.")
                else:
                    nama, username = resolve_member_details(nip_clean)
                    add_member(project_id, username, role, nip=nip_clean, nama=nama, unit=unit)
                    st.success(f"✅ Member berhasil ditambahkan: {nama} ({role} - {unit})")
                    st.rerun()
        else:
            st.info("Hanya PO yang bisa menambah member.")


def render_knowledge(project_id: str) -> None:
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">🧠</div>
                <div class="pm-section-heading">Knowledge Intake</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if can_upload_sources(project_id):
            intake_type = st.radio(
                "Jenis Input",
                ["📄 Dokumen", "📝 Catatan", "🔗 Link URL"],
                horizontal=True,
                label_visibility="collapsed",
            )

            if intake_type == "📄 Dokumen":
                with st.form("upload_doc", clear_on_submit=True):
                    uploaded = st.file_uploader(
                        "Upload dokumen project",
                        type=["pdf", "xlsx", "xls", "csv", "txt", "md", "png", "jpg", "jpeg"],
                        help="PDF, Excel, CSV, text, atau image (flow diagram, ERD, wireframe)",
                    )
                    manual_notes = st.text_area(
                        "Catatan tambahan (opsional)",
                        placeholder="Tambahkan konteks untuk diagram, catatan scope, atau penjelasan dokumen ini...",
                        height=80,
                    )
                    submitted = st.form_submit_button("📤 Submit Dokumen", use_container_width=True, type="primary")
                if submitted and uploaded:
                    extracted = extract_text(uploaded)
                    text = "\n\n".join(part for part in [extracted, manual_notes.strip()] if part)
                    if text.strip():
                        ai_sum = generate_ai_summary(uploaded.name, "file", text)
                        save_document(
                            project_id, uploaded.name, "Official", "All", text,
                            doc_type="file", ai_summary=ai_sum,
                            approval_status="Approved",
                            uploaded_by=st.session_state.get("username", "unknown"),
                            approved_by=st.session_state.get("username", "unknown"),
                        )
                        st.success(f"✅ **{uploaded.name}** berhasil diindeks ke knowledge base.")
                        st.session_state["last_summary"] = ai_sum
                        st.rerun()
                    else:
                        st.warning("Tidak ada teks yang bisa di-index dari file tersebut.")
                elif submitted:
                    st.warning("Pilih file terlebih dahulu.")

            elif intake_type == "📝 Catatan":
                with st.form("upload_note", clear_on_submit=True):
                    note_title = st.text_input("Judul Catatan", placeholder="Contoh: Catatan Rapat Koordinasi 20 Jun")
                    note_text = st.text_area(
                        "Isi Catatan",
                        placeholder="Tuliskan catatan, scope narasi, keputusan rapat, atau konteks penting project...",
                        height=180,
                    )
                    submitted = st.form_submit_button("📝 Simpan Catatan", use_container_width=True, type="primary")
                if submitted:
                    if not note_title.strip() or not note_text.strip():
                        st.warning("⚠️ Judul dan isi catatan wajib diisi.")
                    else:
                        filename = f"note_{slugify(note_title)}.txt"
                        ai_sum = generate_ai_summary(note_title, "note", note_text)
                        save_document(
                            project_id, filename, "Draft", "All", note_text,
                            doc_type="note", ai_summary=ai_sum,
                            approval_status="Approved",
                            uploaded_by=st.session_state.get("username", "unknown"),
                            approved_by=st.session_state.get("username", "unknown"),
                        )
                        st.success(f"✅ Catatan **{note_title}** berhasil disimpan.")
                        st.session_state["last_summary"] = ai_sum
                        st.rerun()

            else:  # Link URL
                with st.form("upload_link", clear_on_submit=True):
                    link_url = st.text_input("URL", placeholder="https://confluence.bca.id/...")
                    link_label = st.text_input("Label / Deskripsi", placeholder="Confluence - BRD QR Settlement")
                    link_desc = st.text_area(
                        "Deskripsi konten link (opsional)",
                        placeholder="Jelaskan apa yang ada di link ini, section mana yang relevan, dll...",
                        height=80,
                    )
                    submitted = st.form_submit_button("🔗 Tambah Link", use_container_width=True, type="primary")
                if submitted:
                    if not link_url.strip():
                        st.warning("⚠️ URL wajib diisi.")
                    else:
                        label_text = link_label.strip() or link_url.strip()
                        content = f"Link: {link_url}\nLabel: {label_text}\n\n{link_desc}"
                        filename = f"link_{slugify(label_text)}.txt"
                        ai_sum = f"🔗 Link reference: [{label_text}]({link_url})" + (f" — {link_desc[:150]}" if link_desc.strip() else "")
                        save_document(
                            project_id, filename, "Informal", "All", content,
                            doc_type="link", ai_summary=ai_sum,
                            approval_status="Approved",
                            uploaded_by=st.session_state.get("username", "unknown"),
                            approved_by=st.session_state.get("username", "unknown"),
                        )
                        st.success(f"✅ Link **{label_text}** berhasil ditambahkan.")
                        st.session_state["last_summary"] = ai_sum
                        st.rerun()
        else:
            st.info("Viewer hanya bisa membaca knowledge yang sudah ada.")

        # Indexed sources table
        docs = project_docs(project_id)
        if docs:
            st.markdown('<div class="pm-section-title" style="margin-top: 24px;">Semua Knowledge yang Diindeks</div>', unsafe_allow_html=True)
            df = pd.DataFrame([{
                "Nama": d["filename"],
                "Tipe": row_get(d, "doc_type") or "file",
                "Uploaded": (d["created_at"] or "")[:10],
                "Oleh": d["uploaded_by"] or "-",
            } for d in docs])
            st.dataframe(df, hide_index=True, use_container_width=True)

    with right:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">✨</div>
                <div class="pm-section-heading">AI Summary & Knowledge Base</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Show last AI summary if just uploaded
        if st.session_state.get("last_summary"):
            st.markdown(
                f"""
                <div class="pm-ai-summary">
                    <div class="pm-ai-summary-label">✨ AI Summary — Terbaru</div>
                    <div class="pm-ai-summary-text">{esc(st.session_state["last_summary"])}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        docs = project_docs(project_id, approved_only=True)

        # Project Context
        if docs:
            st.markdown('<div class="pm-section-title">Project Context</div>', unsafe_allow_html=True)
            doc_types = {}
            for d in docs:
                t = row_get(d, "doc_type") or "file"
                doc_types[t] = doc_types.get(t, 0) + 1
            type_strs = ", ".join(f"{cnt} {t}" for t, cnt in doc_types.items())
            all_text = " ".join(d["text"].lower() for d in docs)
            keywords = []
            for kw in ["deployment", "rollback", "defect", "testing", "release", "migration", "settlement", "UAT", "runbook"]:
                if kw.lower() in all_text:
                    keywords.append(kw)
            st.markdown(
                f"""
                <div class="pm-context-card">
                    <div class="pm-context-label">Konteks Project</div>
                    <div style="color: var(--muted); font-size: .84rem; line-height: 1.6;">
                        <strong style="color: var(--ink);">{len(docs)}</strong> knowledge items ({type_strs})<br/>
                        <strong style="color: var(--ink);">Topik terdeteksi:</strong> {', '.join(keywords[:6]) if keywords else 'Upload lebih banyak dokumen untuk deteksi topik'}<br/>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Knowledge Base list
        st.markdown('<div class="pm-section-title" style="margin-top: 4px;">Knowledge Base</div>', unsafe_allow_html=True)
        if docs:
            for doc in docs[:8]:
                ai_sum = row_get(doc, "ai_summary") or ""
                doc_type = row_get(doc, "doc_type") or "file"
                type_icon = {"file": "📄", "note": "📝", "link": "🔗"}.get(doc_type, "📄")
                type_badge_class = {"file": "official", "note": "note", "link": "link"}.get(doc_type, "draft")
                st.markdown(
                    f"""
                    <div class="pm-source">
                        <span class="pm-pill {type_badge_class}">{type_icon} {doc_type.capitalize()}</span>
                        <div class="pm-source-title">{esc(doc['filename'])}</div>
                        <div class="pm-source-body">{esc(ai_sum[:180]) if ai_sum else esc(doc['text'][:180])}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("Belum ada knowledge. Upload dokumen, tambah catatan, atau masukkan link penting.")


def render_readiness_strip(scores: dict[str, int]) -> None:
    cards = []
    for label, value in scores.items():
        tone = status_tone(value)
        cards.append(
            f'<div class="pm-readiness"><div class="pm-readiness-label">{label}</div><div class="pm-readiness-value">{value}%</div><div class="pm-bar"><div class="pm-fill {tone}" style="width:{value}%"></div></div></div>'
        )
    st.markdown(f'<div class="pm-readiness-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_dashboard_cards(metrics: dict[str, tuple[str, str]]) -> None:
    cards = []
    icons = {"Deadline": "📅", "Remaining": "⏳", "Progress": "📈", "Blockers": "🚧", "Open Defects": "🐛", "Next Gate": "🚀"}
    for label, (value, hint) in metrics.items():
        icon = icons.get(label, "📊")
        cards.append(
            f'<div class="pm-dashboard-card"><div class="pm-dashboard-label">{icon} {esc(label)}</div><div class="pm-dashboard-value">{esc(value)}</div><div class="pm-dashboard-hint">{esc(hint)}</div></div>'
        )
    st.markdown(f'<div class="pm-dashboard-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_chat(project: sqlite3.Row) -> None:
    left, right = st.columns([2.1, 1], gap="large")
    with left:
        # AI Coordinator banner
        st.markdown(
            """
            <div class="pm-coordinator-banner">
                <div class="pm-coordinator-icon">🧠</div>
                <div>
                    <div class="pm-coordinator-name">AI Coordinator</div>
                    <div class="pm-coordinator-desc">Berkoordinasi otomatis dengan Agent IT & Agent UAT untuk memberikan jawaban terbaik berbasis knowledge project.</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for message in chat_history(project["id"], "Coordinator"):
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message["confidence"]:
                    conf = message["confidence"]
                    conf_color = {"High": "#20A77B", "Medium": "#F5A623", "Low": "#E55353"}.get(conf, "#6b7280")
                    st.markdown(f'<small style="color:{conf_color}; font-weight: 700;">Confidence: {conf}</small>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="pm-section-title">Source Reference</div>', unsafe_allow_html=True)
        latest = db_rows(
            """
            select * from chats
            where project_id = ? and agent = 'Coordinator' and role = 'assistant'
            order by created_at desc limit 1
            """,
            (project["id"],),
        )
        if latest and latest[0]["sources"]:
            conf = row_get(latest[0], "confidence")
            if conf:
                conf_color = {"High": "#20A77B", "Medium": "#F5A623", "Low": "#E55353"}.get(conf, "#6b7280")
                st.markdown(f'<span class="pm-chip" style="border-color:{conf_color}40; color:{conf_color};">Confidence: {conf}</span>', unsafe_allow_html=True)
            for source in json.loads(latest[0]["sources"])[:5]:
                label_class = source["label"].lower()
                st.markdown(
                    f"""
                    <div class="pm-source">
                        <div class="pm-source-title">{esc(source["title"])}</div>
                        <div class="pm-source-body">{esc(source["snippet"][:200])}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                """
                <div class="pm-context-card">
                    <div class="pm-context-label">Cara Pakai</div>
                    <div style="color: var(--muted); font-size: .84rem; line-height: 1.6;">
                        Ketik pertanyaan ke AI Coordinator. Referensi sumber dari knowledge base akan muncul di sini setelah AI menjawab.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Agent activity indicator
        st.markdown('<div class="pm-section-title" style="margin-top: 14px;">Agents Aktif</div>', unsafe_allow_html=True)
        for key, agent in AGENTS.items():
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:8px; padding: 6px 0; border-bottom: 1px solid var(--line);">
                    <span style="font-size:1rem;">{agent["icon"]}</span>
                    <div>
                        <div style="font-size:.82rem; font-weight:700; color: var(--ink);">{agent["name"]}</div>
                        <div style="font-size:.74rem; color: var(--muted);">{agent["domain"][:60]}...</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Chat input at the root level of render_chat
    prompt = st.chat_input("Tanya AI Coordinator — koordinasi project, status, blocker, rekomendasi...")
    if prompt:
        store_chat(project["id"], "Coordinator", "user", prompt)
        answer, sources, confidence = answer_question(project["id"], "Coordinator", prompt)
        store_chat(project["id"], "Coordinator", "assistant", answer, sources, confidence)
        st.rerun()


def render_dashboard(project: sqlite3.Row) -> None:
    project_id = project["id"]
    docs = project_docs(project_id, approved_only=True)
    all_docs = project_docs(project_id)
    members = project_members(project_id)
    chats = db_rows("select * from chats where project_id = ?", (project_id,))
    blockers = blocker_signals(project_id)
    milestones = milestone_progress(project_id)
    metrics = dashboard_metrics(project_id)
    scores = readiness_scores(project_id)

    # ── Section 1: Project Overview
    st.markdown(
        """
        <div class="pm-section-header">
            <div class="pm-section-icon">📁</div>
            <div class="pm-section-heading">Project Overview</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    proj_name = project["name"] or "-"
    proj_desc = project["description"] or "Belum ada deskripsi."
    proj_release = project["release_id"] or "TBD"
    proj_change = project["change_id"] or "TBD"
    proj_notes = row_get(project, "notes") or "-"
    try:
        proj_links = json.loads(row_get(project, "knowledge_links") or "[]")
    except Exception:
        proj_links = []

    ov_col1, ov_col2 = st.columns([1.5, 1], gap="large")
    with ov_col1:
        st.markdown(
            f"""
            <div class="pm-shell">
                <div class="pm-section-title">Informasi Project</div>
                <div class="pm-overview-grid">
                    <div class="pm-overview-item">
                        <div class="pm-overview-label">Release ID</div>
                        <div class="pm-overview-val">{esc(proj_release)}</div>
                    </div>
                    <div class="pm-overview-item">
                        <div class="pm-overview-label">Change ID</div>
                        <div class="pm-overview-val">{esc(proj_change)}</div>
                    </div>
                    <div class="pm-overview-item">
                        <div class="pm-overview-label">Members</div>
                        <div class="pm-overview-val">{len(members)} orang</div>
                    </div>
                    <div class="pm-overview-item">
                        <div class="pm-overview-label">Knowledge Docs</div>
                        <div class="pm-overview-val">{len(docs)} approved</div>
                    </div>
                </div>
                <div class="pm-overview-label">Deskripsi</div>
                <div style="font-size:.88rem; color: var(--ink); line-height: 1.5; margin-top: 4px;">{esc(proj_desc)}</div>
                {"" if proj_notes == "-" else f'<div class="pm-overview-label" style="margin-top: 10px;">Notes</div><div style="font-size:.84rem; color: var(--muted); line-height: 1.5; margin-top: 4px;">{esc(proj_notes)}</div>'}
            </div>
            """,
            unsafe_allow_html=True,
        )
    with ov_col2:
        # Team composition
        role_dist = {}
        for m in members:
            role_dist[m["role"]] = role_dist.get(m["role"], 0) + 1
        role_html = "".join(
            f'<div class="pm-project-stat"><span>{role}</span><strong>{cnt}</strong></div>'
            for role, cnt in role_dist.items()
        ) or '<div style="color:var(--muted);font-size:.84rem;">Belum ada member.</div>'
        links_html = "".join(
            f'<div style="font-size:.82rem; padding:4px 0; border-bottom:1px solid var(--line);"><a href="{esc(lnk["url"])}" target="_blank" style="color:var(--brand); text-decoration:none;">↗ {esc(lnk["label"])}</a></div>'
            for lnk in proj_links
        ) or '<div style="color:var(--muted);font-size:.84rem;">Belum ada knowledge links.</div>'
        st.markdown(
            f"""
            <div class="pm-shell">
                <div class="pm-context-label">Komposisi Tim</div>
                {role_html}
                <div class="pm-context-label" style="margin-top: 12px;">Knowledge Links</div>
                {links_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Section 2: Project Summary
    st.markdown(
        """
        <div class="pm-section-header">
            <div class="pm-section-icon">📈</div>
            <div class="pm-section-heading">Project Condition Summary</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    pending_count = len([d for d in all_docs if d["approval_status"] == "Pending"])
    defects = open_defect_count(project_id)
    
    if not docs:
        state = "Tahap Inisiasi"
        state_color = "var(--muted)"
        desc = "Project baru saja dimulai. Silakan unggah dokumen project (BRD, architecture, dll) di tab Knowledge Intake."
    elif defects > 0:
        state = "Membutuhkan Perhatian"
        state_color = "#E55353"
        desc = f"Terdapat {defects} defect atau blocker yang masih terbuka berdasarkan catatan dokumen UAT/IT. Diperlukan tindakan segera dari PIC terkait."
    elif pending_count > 0:
        state = "Menunggu Approval"
        state_color = "var(--accent-amber)"
        desc = f"Sebanyak {pending_count} dokumen masih pending review. PO perlu melakukan approval agar Agent dapat menggunakan data tersebut."
    elif len(members) < 2:
        state = "Kekurangan Anggota"
        state_color = "var(--accent-cyan)"
        desc = "Pengetahuan project sudah tersedia, namun komposisi tim belum memadai. Tambahkan member dari berbagai role (IT, UAT, BA)."
    else:
        state = "On Track"
        state_color = "var(--accent-green)"
        desc = "Proyek berjalan dengan baik. Tidak ada defect terbuka dan dokumen telah disetujui. Tim memiliki pengetahuan teknis dan UAT yang cukup."
        
    blocker_text = "Ada sinyal technical blocker yang perlu diperhatikan." if blockers else "Tidak ditemukan blocker teknikal utama pada source dokumen yang ada."
    
    st.markdown(
        f"""
        <div class="pm-shell" style="border-left: 4px solid {state_color}; padding: 20px 24px; margin-bottom: 24px;">
            <div style="font-size: 0.75rem; text-transform: uppercase; font-weight: 800; color: var(--section); margin-bottom: 8px;">Kondisi Terkini</div>
            <div style="font-size: 1.4rem; font-weight: 900; color: {state_color}; margin-bottom: 12px; line-height: 1.2;">
                {state}
            </div>
            <div style="font-size: 1rem; color: var(--ink); line-height: 1.6; margin-bottom: 16px;">
                {desc}
            </div>
            <div style="font-size: 0.88rem; color: var(--muted); line-height: 1.5; padding-top: 16px; border-top: 1px solid var(--line);">
                <strong>Konteks Ringkas:</strong> Saat ini ada <strong>{len(members)}</strong> anggota tim terdaftar dan <strong>{len(docs)}</strong> dokumen diindeks. {blocker_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="pm-section-title">💡 AI Knowledge Insights</div>', unsafe_allow_html=True)
    approved_docs = project_docs(project_id, approved_only=True)
    if approved_docs:
        html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px;">'
        for doc in approved_docs[:6]:
            filename = doc["filename"]
            doc_type = row_get(doc, "doc_type") or "file"
            ai_sum = row_get(doc, "ai_summary") or "AI belum merangkum dokumen ini."
            type_icon = {"file": "📄", "note": "📝", "link": "🔗"}.get(doc_type, "📄")
            
            html += (
                f'<div class="pm-shell" style="height: 100%; border-left: 3px solid var(--brand); padding: 16px 20px; display: flex; flex-direction: column;">'
                f'<div style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; color: var(--brand); margin-bottom: 6px;">{type_icon} {doc_type}</div>'
                f'<div style="font-size: 0.95rem; font-weight: 700; color: var(--ink); margin-bottom: 8px;">{esc(filename)}</div>'
                f'<div style="font-size: 0.84rem; color: var(--muted); line-height: 1.6; flex-grow: 1;">{esc(ai_sum)}</div>'
                f'</div>'
            )
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.markdown(
            """
            <div class="pm-shell" style="padding: 24px; text-align: center; color: var(--muted);">
                🤖 <strong>Belum ada Dokumen Penunjang.</strong><br/>
                Unggah dokumen (BRD, Runbook, dll.) di tab <strong>Knowledge</strong> untuk mengekstrak scope, dependency, dan insight otomatis dari AI.
            </div>
            """,
            unsafe_allow_html=True,
        )



    # ── Section 3: Action Items
    act_col, rec_col = st.columns([1, 1], gap="large")
    with act_col:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">⚡</div>
                <div class="pm-section-heading">Action Items</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if blockers:
            html = '<div class="pm-shell" style="padding: 12px 20px;">'
            for signal in blockers:
                sev = signal["severity"]
                sev_class = {"High": "pm-severity-high", "Medium": "pm-severity-medium", "Low": "pm-severity-low"}.get(sev, "pm-severity-low")
                if "approval" in signal["title"].lower():
                    owner = "PO"
                elif "defect" in signal["title"].lower():
                    owner = "IT + UAT"
                elif "uat" in signal["title"].lower():
                    owner = "UAT Lead"
                else:
                    owner = "IT Lead"
                html += (
                    f'<div class="pm-action">'
                    f'<div><strong>{esc(signal["title"])}</strong><br/><span>{esc(signal["body"][:100])}</span></div>'
                    f'<div style="text-align:right; white-space:nowrap;">'
                    f'<div class="{sev_class}">{sev}</div>'
                    f'<div style="font-size:.76rem; color:var(--muted); margin-top:2px;">{owner}</div>'
                    f'</div></div>'
                )
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.success("✅ Tidak ada action item aktif dari knowledge base saat ini.")

    # ── Section 4: Recommendations
    with rec_col:
        st.markdown(
            """
            <div class="pm-section-header">
                <div class="pm-section-icon">💡</div>
                <div class="pm-section-heading">Rekomendasi AI</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        # Dynamic recommendations based on state
        recs = []
        pending_count = len([d for d in all_docs if d["approval_status"] == "Pending"])
        defects = open_defect_count(project_id)
        has_it_doc = any(d["agent_scope"] in {"IT", "All"} for d in docs)
        has_uat_doc = any(d["agent_scope"] in {"UAT", "All"} for d in docs)

        if pending_count:
            recs.append(f"📋 Ada **{pending_count} dokumen** menunggu review. PO perlu meng-approve agar AI dapat menggunakannya.")
        if defects:
            recs.append(f"🐛 Terdeteksi **{defects} open defect/blocker** di knowledge base. Koordinasikan resolusi dengan IT dan UAT sebelum release.")
        if not has_it_doc:
            recs.append("⚙️ Belum ada knowledge teknis (IT). Upload runbook, architecture doc, atau BRD teknis agar Agent IT dapat menjawab dengan akurat.")
        if not has_uat_doc:
            recs.append("🧪 Belum ada knowledge UAT. Upload test scenario atau defect log agar Agent UAT dapat mendukung readiness assessment.")
        if len(members) < 3:
            recs.append("👥 Tim masih sedikit. Invite lebih banyak member lintas biro (IT, UAT, BA, PO) untuk kolaborasi yang optimal.")
        if not recs:
            recs.append("✅ Project dalam kondisi baik! Terus update knowledge base dan pantau milestone progress secara rutin.")
            recs.append("💬 Gunakan chat AI Coordinator untuk mendapatkan insight koordinasi real-time berdasarkan dokumen project.")

        if recs:
            html = '<div class="pm-shell" style="padding: 12px 20px;">'
            for rec in recs[:4]:
                html += f'<div class="pm-recommendation" style="margin-bottom: 8px; border: none; background: transparent; padding: 6px 0; border-bottom: 1px solid var(--line); border-radius: 0;"><div class="pm-recommendation-text">{rec}</div></div>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

    st.divider()

    # ── Section 5: FAQ
    st.markdown(
        """
        <div class="pm-section-header">
            <div class="pm-section-icon">❓</div>
            <div class="pm-section-heading">FAQ — Pertanyaan Umum</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    faqs = generate_faq(project_id)
    faq_col1, faq_col2 = st.columns(2, gap="large")
    for i, faq in enumerate(faqs):
        col = faq_col1 if i % 2 == 0 else faq_col2
        with col:
            st.markdown(
                f"""
                <div class="pm-faq-item">
                    <div class="pm-faq-q">❓ {esc(faq["q"])}</div>
                    <div class="pm-faq-a">{esc(faq["a"])}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def main() -> None:
    st.set_page_config(page_title="ProjectMind", page_icon="🧠", layout="wide", initial_sidebar_state="expanded")
    st.session_state.setdefault("dark_mode", False)
    inject_css()

    if not st.session_state.get("authenticated"):
        render_login()
        return

    ensure_storage()
    ensure_sample_project()

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

    # Default tab = Members (index 2)
    tab_members, tab_knowledge, tab_dashboard, tab_chat = st.tabs(["👥 Members", "🧠 Knowledge", "📊 Dashboard", "💬 Chat"])
    with tab_members:
        render_members(project["id"])
    with tab_knowledge:
        render_knowledge(project["id"])
    with tab_dashboard:
        render_dashboard(project)
    with tab_chat:
        render_chat(project)


if __name__ == "__main__":
    main()
