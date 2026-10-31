"""Quote integrity is machine-checkable; scientific support needs human review."""
import json
import re
import unicodedata
from typing import Literal

from pydantic import Field, model_validator
from app.db import one, rows, connection, uid, now
from app.schemas import Inputs
from app.fulltext import read_snapshot, source_artifact


class ClaimInput(Inputs):
    text: str = Field(min_length=8, max_length=5000)
    claim_id: str | None = Field(default=None, max_length=64)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def version(self):
        self.text=self.text.strip()
        if len(self.text)<8:
            raise ValueError("论断至少8个有效字符")
        if (self.claim_id is None) != (self.expected_revision is None):
            raise ValueError("修改论断必须同时提供claim_id和预期版本")
        return self


class EvidenceInput(Inputs):
    claim_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1, max_length=64)
    block_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=8, max_length=2500)
    relation: Literal["supports", "refutes", "context"] = "context"
    conditions: str = Field(default="", max_length=1500)
    notes: str = Field(default="", max_length=2000)
    supersedes_evidence_id: str | None = Field(default=None, max_length=64)


class EvidenceQuery(Inputs):
    claim_id: str | None = Field(default=None, max_length=64)


class ClaimReview(Inputs):
    expected_revision: int = Field(ge=1)
    decision: Literal["supported", "contradicted", "insufficient"]
    reviewer: str = Field(min_length=2, max_length=100)
    notes: str = Field(min_length=10, max_length=3000)
    ocr_transcription_checked: bool = False

    @model_validator(mode="after")
    def meaningful(self):
        if len(self.reviewer.strip())<2 or len(self.notes.strip())<10:
            raise ValueError("须填写审核者和至少10个有效字符的审核说明")
        return self


def normalize_quote(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def quote_match(text, quote):
    normalized = normalize_quote(text)
    query = normalize_quote(quote)
    if len(query) < 8:
        raise ValueError("有效引句至少8个字符")
    position = normalized.find(query)
    return {"matched": position >= 0, "normalized_start": position if position >= 0 else None,
            "normalized_end": position+len(query) if position >= 0 else None,
            "occurrences": normalized.count(query), "normalization": "Unicode NFKC and whitespace only"}


def _get_claim(project_id, claim_id):
    record = one("SELECT * FROM research_claims WHERE id=? AND project_id=?", (claim_id, project_id))
    if not record:
        raise ValueError("论断必须属于当前项目")
    return record


def save_claim(project_id, p):
    ident = p.claim_id or uid()
    timestamp = now()
    with connection() as db:
        if p.claim_id:
            previous = db.execute("SELECT * FROM research_claims WHERE id=? AND project_id=?", (ident, project_id)).fetchone()
            if not previous or previous["revision"] != p.expected_revision:
                raise ValueError("论断不存在或版本已变化，请重新读取")
            revision = p.expected_revision+1
            db.execute("UPDATE research_claims SET text=?,revision=?,updated_at=? WHERE id=?", (p.text, revision, timestamp, ident))
        else:
            revision = 1
            db.execute("INSERT INTO research_claims VALUES(?,?,?,?,?,?)", (ident, project_id, p.text, revision, timestamp, timestamp))
        db.execute("INSERT INTO claim_revisions VALUES(?,?,?,?,?)", (ident, revision, p.text, "claim_text", timestamp))
    return claim_detail(project_id, ident)


def add_evidence(project_id, p):
    _get_claim(project_id, p.claim_id)
    snapshot, content = read_snapshot(project_id, p.snapshot_id)
    source, _, digest = source_artifact(project_id, snapshot["artifact_id"])
    if digest != snapshot["source_sha256"]:
        raise ValueError("原文件版本与全文快照不一致")
    block = next((b for b in content["blocks"] if b["id"] == p.block_id), None)
    if not block:
        raise ValueError("原文段落不存在")
    match = quote_match(block["text"], p.quote)
    if not match["matched"]:
        raise ValueError("引句未出现在指定原文段落；不能用改写文字冒充引句")
    ident, timestamp = uid(), now()
    source_metadata=json.loads(source["metadata"])
    bibliography=source_metadata.get("jats",{})
    annotation = {"quote": p.quote, "relation": p.relation, "conditions": p.conditions, "notes": p.notes,
                  "locator": block["locator"], "source_name": source["name"], "source_sha256": digest,
                  "content_sha256": snapshot["content_sha256"], "quote_check": match,
                  "transcription_history":block.get("transcription_history",[]),
                  "transcription_model":block.get("transcription_model"),
                  "transcription_sha256":block.get("transcription_sha256"),
                  "transcription_prompt_version":block.get("transcription_prompt_version"),
                  "supersedes_evidence_id":p.supersedes_evidence_id,
                  "bibliography":bibliography,"source_url":source_metadata.get("fulltext_source",{}).get("source_url") or ("https://doi.org/"+bibliography["DOI"] if bibliography.get("DOI") else None),
                  "shared_reference":source_metadata.get('shared_reference'),
                  "source_notes":source_metadata.get('source_notes'),
                  "ocr": block.get("ocr", False), "semantic_assessment": "annotation_requires_human_review"}
    with connection() as db:
        previous = db.execute("SELECT * FROM research_claims WHERE id=? AND project_id=?", (p.claim_id, project_id)).fetchone()
        if not previous or previous["revision"] != p.expected_revision:
            raise ValueError("论断版本已变化，请重新读取后关联证据")
        if p.supersedes_evidence_id:
            old = db.execute("SELECT id FROM claim_evidence WHERE id=? AND claim_id=? AND project_id=?",
                             (p.supersedes_evidence_id,p.claim_id,project_id)).fetchone()
            if not old:
                raise ValueError("替换对象必须是当前论断的已有证据")
            for row in db.execute("SELECT data FROM claim_evidence WHERE claim_id=?",(p.claim_id,)):
                if json.loads(row["data"]).get("supersedes_evidence_id")==p.supersedes_evidence_id:
                    raise ValueError("该证据已被替换，请重新读取当前证据")
        duplicate = db.execute("SELECT id FROM claim_evidence WHERE claim_id=? AND snapshot_id=? AND block_id=? AND data=?",
                               (p.claim_id, p.snapshot_id, p.block_id, json.dumps(annotation, ensure_ascii=False))).fetchone()
        if duplicate:
            raise ValueError("相同证据已经关联")
        db.execute("INSERT INTO claim_evidence VALUES(?,?,?,?,?,?,?)", (ident, p.claim_id, project_id, p.snapshot_id, p.block_id, json.dumps(annotation, ensure_ascii=False), timestamp))
        revision = p.expected_revision+1
        db.execute("UPDATE research_claims SET revision=?,updated_at=? WHERE id=?", (revision, timestamp, p.claim_id))
        db.execute("INSERT INTO claim_revisions VALUES(?,?,?,?,?)", (p.claim_id, revision, previous["text"], "evidence_added:"+ident, timestamp))
    return claim_detail(project_id, p.claim_id)


def claim_detail(project_id, claim_id):
    claim = _get_claim(project_id, claim_id)
    evidence = rows("SELECT * FROM claim_evidence WHERE claim_id=? AND project_id=? ORDER BY created_at,id", (claim_id, project_id))
    for item in evidence:
        item["data"] = json.loads(item["data"])
        try:
            snapshot, content = read_snapshot(project_id, item["snapshot_id"])
            _, _, digest = source_artifact(project_id, snapshot["artifact_id"])
            block = next(b for b in content["blocks"] if b["id"] == item["block_id"])
            item["integrity"] = {"source_unchanged": digest == item["data"]["source_sha256"] == snapshot["source_sha256"],
                                 "snapshot_unchanged": snapshot["content_sha256"] == item["data"]["content_sha256"],
                                 "quote_found": quote_match(block["text"], item["data"]["quote"])["matched"]}
        except (ValueError, StopIteration):
            item["integrity"] = {"source_unchanged": False, "snapshot_unchanged": False, "quote_found": False}
    superseded = {e["data"].get("supersedes_evidence_id") for e in evidence}
    # A corrected block invalidates scientific review of its old transcription,
    # while the historical quote remains byte-for-byte reproducible.
    corrections = []
    for row in rows("SELECT content FROM document_snapshots WHERE project_id=?",(project_id,)):
        corrections.extend(json.loads(row["content"]).get("transcription_corrections",[]))
    for item in evidence:
        item["superseded"] = item["id"] in superseded
        item["transcription_outdated"] = any(c["previous_snapshot_id"]==item["snapshot_id"] and c["block_id"]==item["block_id"] for c in corrections)
    reviews = rows("SELECT * FROM claim_reviews WHERE claim_id=? ORDER BY id DESC", (claim_id,))
    current_review = next((r for r in reviews if r["revision"] == claim["revision"]), None)
    active = [e for e in evidence if not e["superseded"]]
    integrity = bool(active) and all(all(item["integrity"].values()) for item in active)
    outdated = any(e["transcription_outdated"] for e in active)
    relations = {item["data"]["relation"] for item in active}
    return {**claim, "evidence": evidence, "reviews": reviews,
            "review_status": current_review["decision"] if current_review and integrity and not outdated else "needs_review",
            "integrity_passed": integrity,"transcription_recheck_required":outdated,
            "conflict": {"opposing_annotations": {"supports", "refutes"}.issubset(relations),
                         "interpretation": "支持/反驳是录入者的判断；需比较体系、条件、方法和误差，不能自动认定科学矛盾。"},
            "history": rows("SELECT * FROM claim_revisions WHERE claim_id=? ORDER BY revision DESC", (claim_id,))}


def check_evidence(project_id, p):
    if p.claim_id:
        return {"claims": [claim_detail(project_id, p.claim_id)]}
    return {"claims": [claim_detail(project_id, row["id"]) for row in rows("SELECT id FROM research_claims WHERE project_id=? ORDER BY updated_at DESC", (project_id,))]}


def review_claim(project_id, claim_id, p):
    claim = claim_detail(project_id, claim_id)
    if claim["revision"] != p.expected_revision:
        raise ValueError("审核对象版本已变化")
    if not claim["integrity_passed"]:
        raise ValueError("缺少有效原文证据或完整性检查未通过，不能提交审核结论")
    if claim["transcription_recheck_required"]:
        raise ValueError("关联转录已有纠正，请以新快照引句替换过期证据后再审核")
    if any(e["data"]["ocr"] for e in claim["evidence"] if not e["superseded"]) and not p.ocr_transcription_checked:
        raise ValueError("机器视觉转录引句须人工对照原图核验后才能审核")
    with connection() as db:
        current = db.execute("SELECT revision FROM research_claims WHERE id=? AND project_id=?", (claim_id, project_id)).fetchone()
        if not current or current["revision"] != p.expected_revision:
            raise ValueError("审核对象版本已变化")
        db.execute("INSERT INTO claim_reviews(claim_id,revision,decision,reviewer,notes,ocr_checked,created_at) VALUES(?,?,?,?,?,?,?)",
                   (claim_id, p.expected_revision, p.decision, p.reviewer, p.notes, int(p.ocr_transcription_checked), now()))
    return claim_detail(project_id, claim_id)


def export_evidence(project_id):
    from pathlib import Path
    from app import config
    from app.documents import artifact_record, export_report
    result = check_evidence(project_id, EvidenceQuery())
    folder = config.ARTIFACTS/uid()
    folder.mkdir()
    path = folder/"evidence-register.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact = artifact_record(project_id, path, metadata={"status": "review_required"})
    body = ["引句出现和文件完整性只证明引用定位；论断支持程度须人工判断。", ""]
    for claim in result["claims"]:
        body.extend([claim["text"], "版本："+str(claim["revision"])+"；审核状态："+claim["review_status"]])
        for item in claim["evidence"]:
            data = item["data"]
            body.extend(["证据状态："+("历史已替换" if item["superseded"] else "当前；转录待复核" if item["transcription_outdated"] else "当前"),
                         "来源："+data["source_name"]+"；位置："+json.dumps(data["locator"], ensure_ascii=False),
                         "文献归属："+json.dumps(data.get("bibliography",{}),ensure_ascii=False),"来源链接："+(data.get("source_url") or "见项目原文件及来源记录"),
                         "来源备注："+json.dumps(data.get('source_notes') or {},ensure_ascii=False),
                         "原文：“"+data["quote"]+"”", "条件/关系标注："+data["conditions"]+" / "+data["relation"],
                         "转录纠正历史："+json.dumps(data.get("transcription_history",[]),ensure_ascii=False),
                         "原件SHA256："+data["source_sha256"]+"；快照："+item["snapshot_id"]])
        body.extend([claim["conflict"]["interpretation"], ""])
    report = export_report(project_id, "全文证据与论断记录", "\n".join(body), ["md", "docx", "pdf"])
    return {"artifacts": [artifact]+report["artifacts"], "claims": len(result["claims"]), "status": "review_required"}
