import os
import zipfile
import xml.etree.ElementTree as ET
import posixpath
from html.parser import HTMLParser
from pypdf import PdfReader

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


class EpubTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.ignore_tags = {'style', 'script', 'head', 'noscript'}
        self.ignore_depth = 0
        self.block_tags = {
            'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
            'li', 'tr', 'blockquote', 'section', 'article', 
            'aside', 'header', 'footer', 'br', 'pre'
        }
        self.headings = []
        self.in_heading = False
        self.heading_parts = []
        
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore_tags:
            self.ignore_depth += 1
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self.in_heading = True
            self.heading_parts = []
            
    def handle_endtag(self, tag):
        if tag in self.ignore_tags:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self.in_heading = False
            heading_text = "".join(self.heading_parts).strip()
            if heading_text:
                self.headings.append(heading_text)
            
    def handle_data(self, data):
        if self.ignore_depth > 0:
            return
        self.text_parts.append(data)
        if self.in_heading:
            self.heading_parts.append(data)
            
    def get_text(self):
        raw_text = "".join(self.text_parts)
        lines = []
        for line in raw_text.split('\n'):
            stripped = line.strip()
            lines.append(stripped)
            
        cleaned_lines = []
        for line in lines:
            if line:
                cleaned_lines.append(line)
            else:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                    
        return "\n".join(cleaned_lines).strip()

def extract_epub_text(file_path: str) -> list[dict]:
    """Extracts plain text grouped by chapters/sections from an EPUB ZIP archive following spine manifest order."""
    blocks = []
    try:
        with zipfile.ZipFile(file_path, 'r') as epub:
            # 1. Locate the OPF root file using container.xml
            opf_path = None
            try:
                if "META-INF/container.xml" in epub.namelist():
                    container_data = epub.read("META-INF/container.xml")
                    root = ET.fromstring(container_data)
                    
                    namespaces = {'ns': 'urn:oasis:names:tc:opendocument:xmlns:container'}
                    rootfile_el = root.find('.//ns:rootfile', namespaces)
                    if rootfile_el is not None:
                        opf_path = rootfile_el.attrib.get('full-path')
                    else:
                        for elem in root.iter():
                            if elem.tag.split('}')[-1] == 'rootfile':
                                opf_path = elem.attrib.get('full-path')
                                break
            except Exception:
                pass
            
            # Fallback to searching zip file list if not resolved
            if not opf_path:
                opf_path = next((f for f in epub.namelist() if f.endswith('.opf')), None)
            
            html_files_in_order = []
            
            # 2. Parse OPF manifest and spine
            if opf_path and opf_path in epub.namelist():
                try:
                    opf_dir = posixpath.dirname(opf_path)
                    opf_data = epub.read(opf_path)
                    opf_root = ET.fromstring(opf_data)
                    
                    # Manifest mapping: id -> (href, media-type)
                    manifest_items = {}
                    for elem in opf_root.iter():
                        tag_name = elem.tag.split('}')[-1]
                        if tag_name == 'item':
                            item_id = elem.attrib.get('id')
                            item_href = elem.attrib.get('href')
                            item_media_type = elem.attrib.get('media-type')
                            if item_id and item_href:
                                manifest_items[item_id] = (item_href, item_media_type)
                                
                    # Spine ordering
                    spine_ids = []
                    for elem in opf_root.iter():
                        tag_name = elem.tag.split('}')[-1]
                        if tag_name == 'itemref':
                            idref = elem.attrib.get('idref')
                            if idref:
                                spine_ids.append(idref)
                                
                    # Map spine IDs to relative paths in the zip
                    for idref in spine_ids:
                        if idref in manifest_items:
                            href, media_type = manifest_items[idref]
                            if href:
                                href_clean = href.split('#')[0].split('?')[0]
                                if opf_dir:
                                    full_path = posixpath.normpath(posixpath.join(opf_dir, href_clean))
                                else:
                                    full_path = posixpath.normpath(href_clean)
                                
                                is_html = (
                                    (media_type and ('html' in media_type or 'xhtml' in media_type)) or
                                    full_path.endswith(('.html', '.xhtml', '.htm'))
                                )
                                if is_html and full_path in epub.namelist():
                                    if full_path not in html_files_in_order:
                                        html_files_in_order.append(full_path)
                except Exception:
                    pass
            
            # Fallback to alphabetical sorting of HTML files
            if not html_files_in_order:
                html_files_in_order = [f for f in epub.namelist() if f.endswith(('.html', '.xhtml', '.htm'))]
                html_files_in_order.sort()
            
            # 3. Extract text from HTML files in order
            for f in html_files_in_order:
                try:
                    content = epub.read(f).decode('utf-8', errors='ignore')
                    parser = EpubTextParser()
                    parser.feed(content)
                    text = parser.get_text()
                    if text:
                        # Extract location from heading or file name fallback
                        location = None
                        if parser.headings:
                            location = parser.headings[0]
                        if not location:
                            base = posixpath.basename(f)
                            name, _ = posixpath.splitext(base)
                            location = name.replace("_", " ").replace("-", " ").title()
                        blocks.append({
                            "text": text,
                            "location": location
                        })
                except Exception:
                    pass
                    
    except Exception as zip_err:
        raise ValueError(f"Failed to read EPUB file: {zip_err}")
        
    return blocks

def extract_pdf_text(file_path: str) -> list[dict]:
    """Extracts plain text grouped by page numbers from a PDF file using PyMuPDF (with pypdf fallback)."""
    pages_data = []
    
    # 1. Try PyMuPDF (fitz) first for robust and fast parsing
    if fitz is not None:
        try:
            doc = fitz.open(file_path)
            for i, page in enumerate(doc):
                text = page.get_text()
                if text and text.strip():
                    pages_data.append({
                        "text": text,
                        "location": f"Page {i + 1}"
                    })
            return pages_data
        except Exception:
            # Fallback to pypdf if PyMuPDF fails on this specific file
            pages_data = []
            
    # 2. Fallback to pypdf
    try:
        reader = PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and text.strip():
                pages_data.append({
                    "text": text,
                    "location": f"Page {i + 1}"
                })
    except Exception as e:
        raise ValueError(f"Failed to read PDF file: {e}")
        
    return pages_data


def parse_obsidian_markdown(content: str) -> tuple[str, list[str]]:
    """
    Strips YAML frontmatter and extracts any tags listed within it.
    Cleans wikilinks [[Target|Display]] -> Display and [[Target]] -> Target.
    """
    import re
    lines = content.split('\n')
    tags = []
    
    if len(lines) > 1 and lines[0].strip() == '---':
        closing_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                closing_idx = i
                break
        
        if closing_idx != -1:
            frontmatter_lines = lines[1:closing_idx]
            content_lines = lines[closing_idx+1:]
            
            in_tags_list = False
            for fl in frontmatter_lines:
                fl_stripped = fl.strip()
                if not fl_stripped:
                    continue
                
                # Check if this line starts a tags block or defines tags directly
                if fl_stripped.startswith(('tags:', 'tag:')):
                    val = fl_stripped.split(':', 1)[1].strip()
                    if val.startswith('[') and val.endswith(']'):
                        parsed_tags = [t.strip().strip('"').strip("'") for t in val[1:-1].split(',')]
                        tags.extend([t for t in parsed_tags if t])
                    elif val:
                        # Space or comma-separated list of tags
                        val_clean = val.replace(',', ' ')
                        parsed_tags = [t.strip() for t in val_clean.split()]
                        tags.extend([t for t in parsed_tags if t])
                    else:
                        in_tags_list = True
                elif in_tags_list and fl_stripped.startswith('- '):
                    tag_val = fl_stripped[2:].strip().strip('"').strip("'")
                    if tag_val:
                        tags.append(tag_val)
                elif in_tags_list and ':' in fl_stripped:
                    in_tags_list = False
                    
            content = '\n'.join(content_lines)
            
    # Normalize tag values (strip leading '#' if present)
    tags = [t.lstrip('#') for t in tags]
    
    # Clean up wikilinks [[link]] or [[link|display]]
    def replace_wikilink(match):
        link_target = match.group(1).strip()
        display_text = match.group(2)
        if display_text:
            return display_text.strip()
        if '#' in link_target:
            link_target = link_target.split('#', 1)[0].strip()
        return link_target

    cleaned_content = re.sub(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', replace_wikilink, content)
    return cleaned_content, tags

def extract_txt_text(file_path: str) -> list[dict]:
    """Extracts plain text from a text or markdown file, splitting by headers if possible."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        raise ValueError(f"Failed to read text file: {e}")
        
    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".md", ".markdown"]:
        # Strip frontmatter and clean wikilinks
        content, tags = parse_obsidian_markdown(content)
        
        lines = content.split('\n')
        blocks = []
        current_header = "Intro"
        current_lines = []
        
        for line in lines:
            if line.startswith(('# ', '## ', '### ')):
                if current_lines:
                    blocks.append({
                        "text": "\n".join(current_lines).strip(),
                        "location": current_header
                    })
                    current_lines = []
                current_header = line.lstrip('#').strip()
            current_lines.append(line)
            
        if current_lines:
            blocks.append({
                "text": "\n".join(current_lines).strip(),
                "location": current_header
            })
            
        blocks = [b for b in blocks if b["text"].strip()]
        if not blocks:
            blocks = [{"text": content, "location": "Full Document"}]
            
        # Append tags to block text to make sure they are indexed
        if tags:
            tag_suffix = "\n\nKeywords: " + ", ".join(tags)
            for b in blocks:
                b["text"] = b["text"] + tag_suffix
                
        return blocks
    else:
        import re
        lines = content.split('\n')
        blocks = []
        current_header = "Full Document"
        current_lines = []
        
        chapter_pattern = re.compile(r'^\s*(chapter|section|part|book)\s+\w+', re.IGNORECASE)
        
        for line in lines:
            if chapter_pattern.match(line):
                if current_lines:
                    blocks.append({
                        "text": "\n".join(current_lines).strip(),
                        "location": current_header
                    })
                    current_lines = []
                current_header = line.strip()
            current_lines.append(line)
            
        if current_lines:
            blocks.append({
                "text": "\n".join(current_lines).strip(),
                "location": current_header
            })
            
        blocks = [b for b in blocks if b["text"].strip()]
        if not blocks:
            blocks = [{"text": content, "location": "Full Document"}]
        return blocks

class HtmlTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.ignore_tags = {'style', 'script', 'head', 'noscript', 'iframe', 'svg'}
        self.ignore_depth = 0
        self.block_tags = {
            'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
            'li', 'tr', 'blockquote', 'section', 'article', 
            'aside', 'header', 'footer', 'br', 'pre'
        }
        self.headings = []
        self.in_heading = False
        self.heading_parts = []
        
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore_tags:
            self.ignore_depth += 1
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self.in_heading = True
            self.heading_parts = []
            
    def handle_endtag(self, tag):
        if tag in self.ignore_tags:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
        if tag in {'h1', 'h2', 'h3', 'h4'}:
            self.in_heading = False
            heading_text = "".join(self.heading_parts).strip()
            if heading_text:
                self.headings.append(heading_text)
            
    def handle_data(self, data):
        if self.ignore_depth > 0:
            return
        self.text_parts.append(data)
        if self.in_heading:
            self.heading_parts.append(data)
            
    def get_text(self):
        raw_text = "".join(self.text_parts)
        lines = []
        for line in raw_text.split('\n'):
            lines.append(line.strip())
            
        cleaned_lines = []
        for line in lines:
            if line:
                cleaned_lines.append(line)
            else:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                    
        return "\n".join(cleaned_lines).strip()

def extract_html_text(file_path: str) -> list[dict]:
    """Extracts text from an HTML file, splitting by headings if present."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        raise ValueError(f"Failed to read HTML file: {e}")
        
    parser = HtmlTextParser()
    parser.feed(content)
    text = parser.get_text()
    if not text:
        return []
        
    blocks = []
    if parser.headings:
        import re
        escaped_headings = [re.escape(h) for h in parser.headings if h.strip()]
        if escaped_headings:
            pattern = "|".join(f"(?:\\b{h}\\b)" for h in escaped_headings)
            try:
                sections = re.split(f"({pattern})", text)
                current_location = "Intro"
                current_text = ""
                
                for item in sections:
                    if not item:
                        continue
                    item_strip = item.strip()
                    if item_strip in parser.headings:
                        if current_text.strip():
                            blocks.append({
                                "text": current_text.strip(),
                                "location": current_location
                            })
                        current_location = item_strip
                        current_text = ""
                    else:
                        current_text += "\n" + item
                        
                if current_text.strip():
                    blocks.append({
                        "text": current_text.strip(),
                        "location": current_location
                    })
            except Exception:
                blocks = [{"text": text, "location": "Full Document"}]
        else:
            blocks = [{"text": text, "location": "Full Document"}]
    else:
        blocks = [{"text": text, "location": "Full Document"}]
        
    if not blocks:
        blocks = [{"text": text, "location": "Full Document"}]
    return blocks

def extract_docx_text(file_path: str) -> list[dict]:
    """Extracts paragraph text from a DOCX file using standard zipfile and xml parsing."""
    try:
        with zipfile.ZipFile(file_path) as docx:
            xml_content = docx.read('word/document.xml')
            root = ET.fromstring(xml_content)
            
            namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            
            paragraphs = []
            for p in root.findall('.//w:p', namespaces):
                p_text = []
                for t in p.findall('.//w:t', namespaces):
                    if t.text:
                        p_text.append(t.text)
                if p_text:
                    paragraphs.append("".join(p_text))
                    
            full_text = "\n\n".join(paragraphs)
            if not full_text.strip():
                return []
                
            blocks = []
            current_lines = []
            current_header = "Section 1"
            
            for p in paragraphs:
                p_stripped = p.strip()
                if not p_stripped:
                    continue
                
                is_header = False
                if len(p_stripped) < 80 and (p_stripped.lower().startswith(('chapter', 'section', 'part', 'introduction', 'conclusion', 'summary')) or p_stripped.isupper()):
                    is_header = True
                    
                if is_header:
                    if current_lines:
                        blocks.append({
                            "text": "\n".join(current_lines).strip(),
                            "location": current_header
                        })
                        current_lines = []
                    current_header = p_stripped
                else:
                    current_lines.append(p)
                    
            if current_lines:
                blocks.append({
                    "text": "\n".join(current_lines).strip(),
                    "location": current_header
                })
                
            if not blocks:
                blocks = [{"text": full_text, "location": "Full Document"}]
            return blocks
    except Exception as e:
        raise ValueError(f"Failed to read DOCX file: {e}")

def extract_org_text(file_path: str) -> list[dict]:
    """Extracts text from an Emacs Org-mode file, splitting by headings (* Heading)."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        raise ValueError(f"Failed to read ORG file: {e}")
        
    lines = content.split('\n')
    blocks = []
    current_header = "Intro"
    current_lines = []
    
    import re
    heading_pattern = re.compile(r'^\*+\s+(.+)$')
    
    for line in lines:
        match = heading_pattern.match(line)
        if match:
            if current_lines:
                blocks.append({
                    "text": "\n".join(current_lines).strip(),
                    "location": current_header
                })
                current_lines = []
            current_header = match.group(1).strip()
        current_lines.append(line)
        
    if current_lines:
        blocks.append({
            "text": "\n".join(current_lines).strip(),
            "location": current_header
        })
        
    blocks = [b for b in blocks if b["text"].strip()]
    if not blocks:
        blocks = [{"text": content, "location": "Full Document"}]
    return blocks

def _clean_meta_field(value) -> str | None:
    """A metadata field usable as a display string, or None. Rejects the junk
    real-world PDF producers leave behind (filenames, template markers)."""
    import re
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    # Trailing pirate-site / drive suffixes: "Title - PDFDrive.com"
    v = re.sub(r"\s*[-–—(]\s*(www\.)?[A-Za-z0-9-]+\.(com|org|net|pub|info)\)?\s*$", "", v).strip()
    if not v or len(v) < 3 or len(v) > 200 or v.lower() in ("untitled", "unknown", "none", "me"):
        return None
    if "_" in v or "*" in v:
        return None  # template/producer strings ("**Final_The War of Art_6x9**")
    if " " not in v and re.search(r"\.[A-Za-z]{2,5}$", v):
        return None  # a bare filename ("runfm.dvi", "book.indd")
    return v


def extract_metadata(file_path: str) -> dict:
    """Best-effort {'title','author'} from embedded document metadata.
    Missing/junk fields are omitted; unsupported types return {}."""
    ext = os.path.splitext(file_path)[1].lower()
    meta = {}
    try:
        if ext == ".pdf":
            title = author = None
            if fitz is not None:
                try:
                    doc = fitz.open(file_path)
                    m = doc.metadata or {}
                    title, author = m.get("title"), m.get("author")
                except Exception:
                    pass
            if title is None and author is None:
                info = PdfReader(file_path).metadata
                if info:
                    title, author = info.title, info.author
            meta = {"title": _clean_meta_field(title), "author": _clean_meta_field(author)}
        elif ext == ".epub":
            with zipfile.ZipFile(file_path, "r") as epub:
                opf_path = None
                if "META-INF/container.xml" in epub.namelist():
                    root = ET.fromstring(epub.read("META-INF/container.xml"))
                    for elem in root.iter():
                        if elem.tag.split("}")[-1] == "rootfile":
                            opf_path = elem.attrib.get("full-path")
                            break
                if not opf_path:
                    opf_path = next((f for f in epub.namelist() if f.endswith(".opf")), None)
                if opf_path and opf_path in epub.namelist():
                    opf_root = ET.fromstring(epub.read(opf_path))
                    title = author = None
                    for elem in opf_root.iter():
                        tag = elem.tag.split("}")[-1]
                        if tag == "title" and title is None:
                            title = elem.text
                        elif tag == "creator" and author is None:
                            author = elem.text
                    meta = {"title": _clean_meta_field(title), "author": _clean_meta_field(author)}
    except Exception:
        return {}
    return {k: v for k, v in meta.items() if v}


def extract_text(file_path: str) -> list[dict]:
    """Extracts text chunks and their locations from a file based on its extension."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
        
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".pdf":
        return extract_pdf_text(file_path)
    elif ext == ".epub":
        return extract_epub_text(file_path)
    elif ext in [".txt", ".md", ".markdown"]:
        return extract_txt_text(file_path)
    elif ext == ".docx":
        return extract_docx_text(file_path)
    elif ext in [".html", ".htm"]:
        return extract_html_text(file_path)
    elif ext == ".org":
        return extract_org_text(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
