"""Full-text snapshots from native text and structured document sources."""
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

from pydantic import Field, model_validator
from typing import Literal
from app import config
from app.db import one, rows, execute, uid, now
from app.schemas import Inputs

class ExtractInput(Inputs):
    artifact_id: str = Field(min_length=1, max_length=64)
    first_page: int = Field(default=1, ge=1, le=600)
    last_page: int | None = Field(default=None, ge=1, le=600)
    image_mode: Literal["native", "ai"] = "native"
    tables: bool = True


class DocumentReadInput(Inputs):
    snapshot_id: str = Field(min_length=1, max_length=64)
    page: int | None = Field(default=None, ge=1, le=600)
    block_ids: list[str] = Field(default_factory=list, max_length=50)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class FulltextImportInput(Inputs):
    pmcid: str = Field(pattern=r"^PMC[1-9][0-9]{0,10}$")


class TranscriptionCorrection(Inputs):
    block_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=30000)


class CorrectTranscriptionInput(Inputs):
    reviewer: str = Field(min_length=2, max_length=100)
    notes: str = Field(min_length=10, max_length=3000)
    corrections: list[TranscriptionCorrection] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def meaningful(self):
        if len(self.reviewer.strip()) < 2 or len(self.notes.strip()) < 10:
            raise ValueError("须填写转录核对者和至少10个有效字符的说明")
        if len({item.block_id for item in self.corrections}) != len(self.corrections):
            raise ValueError("同一段落只能提交一次纠正")
        if any(not item.text.strip() for item in self.corrections):
            raise ValueError("纠正后的文字不能为空")
        return self


def import_open_fulltext(project_id, p):
    import httpx
    from app.documents import ingest, public_artifact
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
    try:
        with httpx.Client(timeout=35, follow_redirects=False, headers={"User-Agent":"ChemELLM/0.1 evidence research"}) as client:
            response = client.get(base+"search", params={"query":"PMCID:"+p.pmcid,"format":"json","resultType":"core"})
            response.raise_for_status()
            records = response.json().get("resultList", {}).get("result", [])
            reference = next((r for r in records if r.get("pmcid") == p.pmcid), None)
            if not reference or reference.get("isOpenAccess") != "Y":
                raise ValueError("未确认该PMCID为开放获取；可上传有权使用的原文")
            with client.stream("GET", base+p.pmcid+"/fullTextXML") as stream:
                stream.raise_for_status()
                parts, length = [], 0
                for part in stream.iter_bytes():
                    length += len(part)
                    if length > config.MAX_UPLOAD:
                        raise ValueError("开放全文超过25 MiB限制")
                    parts.append(part)
                content = b"".join(parts)
    except (httpx.HTTPError, KeyError) as exc:
        raise ValueError("开放全文服务请求失败；未用题录或生成内容代替全文："+type(exc).__name__) from exc
    parsed = extract_jats(content)
    record = ingest(project_id, p.pmcid+".xml", content)
    metadata = {**record["metadata"], "fulltext_source": {"provider":"Europe PMC", "pmcid":p.pmcid,
                "source_url":base+p.pmcid+"/fullTextXML", "metadata_url":str(response.url),
                "DOI":reference.get("doi"), "title":reference.get("title"), "open_access_metadata":"Y",
                "license_text":parsed["license_text"], "retrieved_at":now()}}
    execute("UPDATE artifacts SET metadata=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), record["id"]))
    return {"artifact":public_artifact(one("SELECT * FROM artifacts WHERE id=?", (record["id"],))),
            "extraction":extract_document(project_id, ExtractInput(artifact_id=record["id"]))}


def source_artifact(project_id, artifact_id):
    record = one("SELECT * FROM artifacts WHERE id=? AND project_id=?", (artifact_id, project_id))
    if not record:
        raise ValueError("文献必须属于当前项目")
    path = Path(record["path"]).resolve()
    if not path.is_relative_to(config.ARTIFACTS) or not path.is_file():
        raise ValueError("文献原文件缺失或路径无效")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != json.loads(record["metadata"]).get("sha256"):
        raise ValueError("文献原文件哈希发生变化，请重新导入")
    return record, path, digest


def _block(blocks, text, kind, locator, **extra):
    if text and text.strip():
        blocks.append({"id": "b"+str(len(blocks)+1).zfill(5), "kind": kind,
                       "text": text.strip(), "locator": locator, **extra})


def extract_jats(content):
    from defusedxml.ElementTree import fromstring
    try:
        root = fromstring(content, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise ValueError("XML无法安全解析；不支持实体或外部资源") from exc
    if root.tag.rsplit("}", 1)[-1] != "article":
        raise ValueError("仅支持JATS article全文XML")
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    def text(element):
        return "".join(element.itertext()).strip() if element is not None else ""
    blocks = []
    title = text(root.find(".//article-title"))
    _block(blocks, title, "title", {"section": "title", "page": None})
    for abstract in root.findall(".//abstract"):
        _block(blocks, text(abstract), "abstract", {"section": "abstract", "page": None})
    def walk(element, section="body"):
        if element.tag == "sec":
            section = text(element.find("title")) or section
        locator = {"page": None, "section": section, "anchor": element.get("id")}
        if element.tag == "table-wrap":
            cells = [[text(c) for c in list(row) if c.tag in {"th", "td"}] for row in element.findall(".//tr")]
            _block(blocks, text(element.find("label"))+" "+text(element.find("caption"))+"\n"+"\n".join("\t".join(row) for row in cells), "table", locator, cells=cells)
            return
        if element.tag == "disp-formula":
            tex = element.find(".//tex-math")
            _block(blocks, text(tex) if tex is not None else text(element), "equation", locator,
                   representation="source_tex" if tex is not None else "source_mathml_text_requires_review")
            return
        if element.tag in {"p", "fig"}:
            _block(blocks, text(element), "figure_caption" if element.tag == "fig" else "paragraph", locator)
            # Inline formulas remain in paragraph text; displayed formulas are indexed separately.
            for formula in element.findall(".//disp-formula"):
                walk(formula, section)
            return
        for child in element:
            walk(child, section)
    body = root.find("body")
    if body is not None:
        walk(body)
    licenses = [text(v) for v in root.findall(".//license")]
    dois = [text(v) for v in root.findall(".//article-id") if v.get("pub-id-type") == "doi"]
    authors=[]
    for contributor in root.findall(".//article-meta/contrib-group/contrib"):
        if contributor.get("contrib-type") not in {None,"author"}:
            continue
        name=contributor.find("name")
        authors.append((text(name.find("given-names"))+" "+text(name.find("surname"))).strip() if name is not None else text(contributor.find("collab")))
    return {"blocks": blocks, "title": title, "DOI": dois[0] if dois else None, "license_text": licenses,
            "authors":[v for v in authors if v],"journal":text(root.find(".//journal-title")),"year":text(root.find(".//pub-date/year")),
            "warnings": ["XML段落/表格及源公式按原文提取；没有出版版页码，不伪造页码。", "图像仅索引图注；未自动解释图像内实验数据。"]}


def _pdf(path, p):
    import pdfplumber
    blocks, warnings, processed, vision_requests = [], [], [], []
    vision_versions = []
    with pdfplumber.open(path) as document:
        total_pages = len(document.pages)
        if p.image_mode == 'ai' and p.last_page is not None and p.last_page != p.first_page:
            raise ValueError('AI视觉模式一次只处理一页，以便准确计入模型请求预算；请按页调用')
        last = (p.first_page if p.image_mode == 'ai' else p.last_page or min(len(document.pages), p.first_page+19))
        if p.image_mode == 'ai' and last < total_pages:
            warnings.append(f'AI视觉模式本次仅处理第{last}页；PDF共{total_pages}页，其他页面尚未读取，须继续逐页调用。')
        if p.first_page > last or last > len(document.pages) or last-p.first_page >= 50:
            raise ValueError("页码范围无效；每次最多提取50页，可分段建立快照")
        for number in range(p.first_page, last+1):
            page = document.pages[number-1]
            text = page.extract_text() or ""
            locator = {"page": number, "bbox": [0, 0, float(page.width), float(page.height)], "coordinates": "PDF points from top-left"}
            if text.strip():
                _block(blocks, text, "page_text", locator)
            elif p.image_mode == "ai":
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(path)
                image = None
                try:
                    rendered = pdf[number-1]
                    try:
                        scale = min(2, (25_000_000/(page.width*page.height))**0.5)
                        bitmap = rendered.render(scale=scale)
                        try:
                            image = bitmap.to_pil().copy()
                        finally:
                            bitmap.close()
                    finally:
                        rendered.close()
                    from app.vision import transcribe_image
                    result = transcribe_image(image, source_name=path.name, page=number)
                    vision_requests.append({k: result[k] for k in ('model', 'usage', 'status', 'request_made') if k in result})
                    if result.get('status') == 'transcribed_not_verified':
                        _block(blocks, result['text'], 'ai_image_transcription', locator, ocr=True,
                               transcription_review_required=True, transcription_model=result['model'],
                               transcription_sha256=result['response_sha256'],
                               transcription_image_sha256=result['image_sha256'],
                               transcription_prompt_version=result['prompt_version'])
                        vision_versions.append({'model': result['model'], 'prompt_version': result['prompt_version'],
                                                'image_sha256': result['image_sha256'],
                                                'response_sha256': result['response_sha256']})
                    else:
                        warnings.append(f"第{number}页AI视觉转录不可用：{result.get('error', '未知错误')}")
                        if result.get('request_made'):
                            vision_versions.append({'model': result.get('model'), 'status': 'unavailable',
                                                    'image_sha256': result.get('image_sha256')})
                finally:
                    if image is not None:
                        image.close()
                    pdf.close()
            elif not text.strip():
                warnings.append(f"第{number}页没有可提取的原生文本；仅保留原件")
            if p.tables and text.strip():
                try:
                    for table in page.find_tables():
                        cells = [[str(c) if c is not None else "" for c in row] for row in table.extract()]
                        _block(blocks, "\n".join("\t".join(row) for row in cells), "table",
                               {**locator, "bbox": list(table.bbox)}, cells=cells, structure_review_required=True)
                except (ValueError, TypeError) as exc:
                    warnings.append(f"第{number}页表格提取失败：{type(exc).__name__}")
            if text.strip():
                for line in page.extract_text_lines(return_chars=False):
                    if any(symbol in line["text"] for symbol in ("=", "∑", "∫")):
                        _block(blocks, line["text"], "equation_candidate", {**locator, "bbox": [line[k] for k in ("x0", "top", "x1", "bottom")]}, representation="PDF_text_requires_review")
            processed.append(number)
    return {"blocks": blocks, "processed_pages": processed, "total_pages": total_pages,
            "warnings": warnings+["PDF原生文本、表格和公式候选须对照原版；AI图像转录仍须逐字核验。"],
            "vision_requests": vision_requests, "vision_versions": vision_versions}


def extract_document(project_id, p):
    record, path, digest = source_artifact(project_id, p.artifact_id)
    kind = path.suffix.lower()
    versions = {}
    vision_requests = []
    if kind == ".pdf":
        content = _pdf(path, p)
        versions["pdfplumber"] = importlib.metadata.version("pdfplumber")
        vision_requests.extend(content.get("vision_requests", []))
        if content.get("vision_versions"):
            versions["ai_vision_transcription"] = content["vision_versions"]
    elif kind == ".xml":
        content = extract_jats(path.read_bytes())
    elif kind == ".html":
        from app.public_sources import extract_html
        meta = json.loads(record["metadata"])
        source = meta.get("public_source", {})
        headers = source.get("headers", {})
        content = extract_html(path.read_bytes(), source.get("source_url", ""), headers.get("content-type", ""))
        versions["html_extractor"] = content["extractor"]
    elif kind in {".png", ".jpg", ".jpeg"}:
        from PIL import Image
        blocks, warnings = [], []
        if p.image_mode == "ai":
            from app.vision import transcribe_image
            with Image.open(path) as image:
                result = transcribe_image(image, source_name=path.name)
            vision_requests.append({k: result[k] for k in ('model', 'usage', 'status', 'request_made') if k in result})
            if result.get('status') == 'transcribed_not_verified':
                _block(blocks, result['text'], 'ai_image_transcription',
                       {'page': None, 'section': 'AI image transcription'}, ocr=True,
                       transcription_review_required=True, transcription_model=result['model'],
                       transcription_sha256=result['response_sha256'],
                       transcription_image_sha256=result['image_sha256'],
                       transcription_prompt_version=result['prompt_version'])
                versions['ai_vision_transcription'] = [{'model': result['model'],
                    'prompt_version': result['prompt_version'], 'image_sha256': result['image_sha256'],
                    'response_sha256': result['response_sha256']}]
            else:
                warnings.append('AI视觉转录不可用：' + result.get('error', '未知错误'))
                if result.get('request_made'):
                    versions['ai_vision_transcription'] = [{'model': result.get('model'),
                        'status': 'unavailable', 'image_sha256': result.get('image_sha256')}]
        else:
            warnings.append('没有运行图像文字识别；原始图像已保留。可明确选择AI视觉转录，结果仍须人工核验。')
        content = {'blocks': blocks, 'warnings': warnings}
    elif kind in {".txt", ".md", ".docx", ".pptx"}:
        meta = json.loads(record["metadata"])
        pages = meta.get("pages") or [{"page": None, "text": record["text"] or ""}]
        blocks = []
        for page in pages:
            for paragraph in re.split(r"\n\s*\n", page["text"]):
                _block(blocks, paragraph, "paragraph", {"page": page["page"], "section": "document text"})
        content = {"blocks": blocks, "warnings": ["DOCX或纯文本没有可靠出版版页码；PPTX页码指幻灯片。"]}
    else:
        raise ValueError("全文提取支持PDF、JATS XML、HTML、文本、DOCX/PPTX和PNG/JPEG")
    if len(content["blocks"]) > 10000 or sum(len(b["text"]) for b in content["blocks"]) > 2_000_000:
        raise ValueError("提取内容过多，请缩小页码范围")
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    ident = uid()
    content_hash = hashlib.sha256(encoded.encode()).hexdigest()
    execute("INSERT INTO document_snapshots VALUES(?,?,?,?,?,?,?,?)",
            (ident, project_id, p.artifact_id, digest, content_hash, json.dumps(versions), encoded, now()))
    vision_status = 'unavailable' if any(r.get('status') == 'unavailable' for r in vision_requests) else 'transcribed_not_verified' if vision_requests else 'not_requested'
    return {"snapshot_id": ident, "artifact_id": p.artifact_id, "source_sha256": digest, "content_sha256": content_hash,
            "blocks": len(content["blocks"]), "kinds": {kind: sum(b["kind"] == kind for b in content["blocks"]) for kind in sorted({b["kind"] for b in content["blocks"]})},
            "processed_pages": content.get("processed_pages"), "total_pages": content.get("total_pages"), "warnings": content["warnings"],
            "extractor_versions": versions, "vision_requests": vision_requests, 'vision_status': vision_status,
            'status': 'unavailable' if vision_status == 'unavailable' else 'extracted'}


def read_snapshot(project_id, snapshot_id):
    snapshot = one("SELECT * FROM document_snapshots WHERE id=? AND project_id=?", (snapshot_id, project_id))
    if not snapshot:
        raise ValueError("全文快照必须属于当前项目")
    if hashlib.sha256(snapshot["content"].encode()).hexdigest() != snapshot["content_sha256"]:
        raise ValueError("全文快照完整性校验失败")
    return snapshot, json.loads(snapshot["content"])


def read_document(project_id, p):
    snapshot, content = read_snapshot(project_id, p.snapshot_id)
    _, _, actual_hash = source_artifact(project_id, snapshot["artifact_id"])
    if actual_hash != snapshot["source_sha256"]:
        raise ValueError("全文快照与原文件版本不一致")
    blocks = content["blocks"]
    if p.page is not None:
        blocks = [b for b in blocks if b["locator"].get("page") == p.page]
    if p.block_ids:
        selected = set(p.block_ids)
        blocks = [b for b in blocks if b["id"] in selected]
        if {b["id"] for b in blocks} != selected:
            raise ValueError("段落编号或页码不匹配")
    return {"snapshot_id": p.snapshot_id, "artifact_id": snapshot["artifact_id"], "source_sha256": actual_hash,
            "total_blocks": len(blocks), "blocks": blocks[p.offset:p.offset+p.limit], "warnings": content["warnings"],
            "derived_from":content.get("derived_from"),
            "transcription_corrections":content.get("transcription_corrections",[]),
            "bibliography":{k:content.get(k) for k in ("title","DOI","authors","journal","year","license_text")}}


def correct_transcription(project_id, snapshot_id, p):
    """Explicit human transcription creates a new snapshot; no scientific approval."""
    snapshot, content = read_snapshot(project_id, snapshot_id)
    _, _, actual_hash = source_artifact(project_id, snapshot["artifact_id"])
    if actual_hash != snapshot["source_sha256"]:
        raise ValueError("原文件版本已变化，不能纠正旧快照")
    blocks = {b["id"]: b for b in content["blocks"]}
    changes = []
    timestamp = now()
    for correction in p.corrections:
        block = blocks.get(correction.block_id)
        if not block or not block.get("ocr"):
            raise ValueError("只能纠正标记为机器图像转录的段落")
        if correction.text.strip() == block["text"]:
            raise ValueError("纠正文字与原转录相同")
        changes.append({"block_id":block["id"], "previous_text":block["text"],
                        "text":correction.text.strip(), "reviewer":p.reviewer.strip(), "notes":p.notes.strip(),
                        "created_at":timestamp, "previous_snapshot_id":snapshot_id,
                        "scope":"human transcription; not scientific approval"})
    for change in changes:
        block = blocks[change["block_id"]]
        block["text"] = change["text"]
        block.setdefault("transcription_history", []).append(change)
        # Preserve the model transcription flag; a correction does not certify its accuracy.
    content["derived_from"] = snapshot_id
    content.setdefault("transcription_corrections", []).extend(changes)
    content["warnings"] = list(dict.fromkeys(content["warnings"]+[
        "含人工纠正的机器视觉转录；旧证据保留原快照，须另行核对，不自动升级科学审核。"
    ]))
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > 12_000_000:
        raise ValueError("纠正历史过大，请重新导入文献")
    ident = uid()
    versions = json.loads(snapshot["extractor_versions"])
    versions["manual_transcription"] = "1"
    execute("INSERT INTO document_snapshots VALUES(?,?,?,?,?,?,?,?)",
            (ident, project_id, snapshot["artifact_id"], actual_hash, hashlib.sha256(encoded.encode()).hexdigest(),
             json.dumps(versions), encoded, timestamp))
    return {"snapshot_id":ident, "derived_from":snapshot_id, "corrected_blocks":[c["block_id"] for c in changes],
            "status":"transcription_corrected; scientific_review_required"}


def snapshot_list(project_id):
    return rows("SELECT s.id,s.artifact_id,s.source_sha256,s.content_sha256,s.created_at,a.name FROM document_snapshots s JOIN artifacts a ON a.id=s.artifact_id WHERE s.project_id=? ORDER BY s.created_at DESC", (project_id,))


def search_fulltext(project_id, query, limit=5):
    import math
    from collections import Counter
    def tokens(text):
        english = re.findall(r"[a-z][a-z0-9_-]*|\d+(?:\.\d+)?", text.lower())
        english = [v[:-1] if len(v) > 4 and v.endswith("s") else v for v in english]
        chinese = re.findall(r"[\u3400-\u9fff]+", text)
        return english+[word[i:i+2] for word in chinese for i in range(max(1,len(word)-1))]
    terms = set(tokens(query))
    documents, seen = [], set()
    for snapshot in rows("SELECT s.*,a.name FROM document_snapshots s JOIN artifacts a ON a.id=s.artifact_id WHERE s.project_id=? ORDER BY s.created_at DESC", (project_id,)):
        for block in json.loads(snapshot["content"])["blocks"]:
            key = (snapshot["artifact_id"], snapshot["source_sha256"], json.dumps(block["locator"],sort_keys=True), block["text"])
            if key in seen:
                continue
            seen.add(key)
            words = Counter(tokens(block["text"]))
            documents.append((snapshot,block,words))
    if not documents or not terms:
        return []
    frequencies = {term:sum(term in words for _,_,words in documents) for term in terms}
    average = sum(sum(words.values()) for _,_,words in documents)/len(documents)
    found = []
    for snapshot,block,words in documents:
        size = sum(words.values())
        score = sum(math.log(1+(len(documents)-frequencies[term]+.5)/(frequencies[term]+.5))*words[term]*2.2/(words[term]+1.2*(.25+.75*size/max(average,1))) for term in terms if words[term])
        if not score:
            continue
        positions = [block["text"].lower().find(t) for t in terms if block["text"].lower().find(t) >= 0]
        start = max(0,min(positions,default=0)-180)
        found.append({"artifact_id":snapshot["artifact_id"], "name":snapshot["name"], "snapshot_id":snapshot["id"],
                      "block_id":block["id"], "citation_id":snapshot["id"]+":"+block["id"], "page":block["locator"].get("page"),
                      "locator":block["locator"], "kind":block["kind"], "text":block["text"][start:start+1800], "score":score,
                      "source_sha256":snapshot["source_sha256"], "ocr":block.get("ocr",False)})
    return sorted(found,key=lambda r:r["score"],reverse=True)[:limit]
