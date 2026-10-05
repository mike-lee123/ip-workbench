import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import os
import io
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont

# ==============================================================================
# 一、 核心資料結構與專利檢索邏輯
# ==============================================================================
@dataclass
class TechnicalPillar:
    name: str
    en_keywords: List[str] = field(default_factory=list)
    zh_keywords: List[str] = field(default_factory=list)

class PatentSearchBuilder:
    def __init__(self, target_title: str):
        self.target_title = target_title
        self.ipc_classes: List[str] = []
        self.cpc_classes: List[str] = []
        self.pillars: List[TechnicalPillar] = []

    def add_ipc(self, *ipc_codes: str) -> "PatentSearchBuilder":
        self.ipc_classes.extend([code.strip() for code in ipc_codes if code.strip()])
        return self

    def add_cpc(self, *cpc_codes: str) -> "PatentSearchBuilder":
        self.cpc_classes.extend([code.strip() for code in cpc_codes if code.strip()])
        return self

    def add_pillar(self, name: str, en_keywords: List[str], zh_keywords: List[str]) -> "PatentSearchBuilder":
        self.pillars.append(
            TechnicalPillar(
                name=name,
                en_keywords=[kw.strip() for kw in en_keywords if kw.strip()],
                zh_keywords=[kw.strip() for kw in zh_keywords if kw.strip()]
            )
        )
        return self

    def to_google_patents_query(self) -> str:
        pillar_blocks = []
        for p in self.pillars:
            if p.en_keywords:
                formatted_kws = []
                for kw in p.en_keywords[:3]:
                    clean_kw = re.sub(r'[\";*]', '', kw).strip()
                    if not clean_kw:
                        continue
                    tokens = clean_kw.split()
                    if len(tokens) == 1:
                        formatted_kws.append(clean_kw)
                    elif len(tokens) == 2:
                        formatted_kws.append(f'"{clean_kw}"')
                    else:
                        formatted_kws.append(f'"{tokens[-2]} {tokens[-1]}"')
                
                if formatted_kws:
                    pillar_blocks.append(f"({' OR '.join(formatted_kws)})")

        keyword_part = " AND ".join(pillar_blocks) if pillar_blocks else ""

        all_classes = self.cpc_classes or self.ipc_classes
        if all_classes:
            clean_classes = []
            for c in all_classes:
                raw_c = re.sub(r'[\s;*]', '', c).strip().upper()
                if raw_c:
                    clean_classes.append(raw_c)
            
            if clean_classes:
                classes_str = f"({' OR '.join(clean_classes)})"
                if keyword_part:
                    final_q = f"{keyword_part} AND {classes_str}"
                else:
                    final_q = classes_str
            else:
                final_q = keyword_part
        else:
            final_q = keyword_part

        return final_q.rstrip("; ").strip()

    def to_gpss_query(self, search_fields: str = "TI,AB,CL") -> str:
        pillar_blocks = []
        for p in self.pillars:
            all_kw = p.zh_keywords + p.en_keywords
            if all_kw:
                formatted = [f'"{re.sub(r"[\";*]", "", kw).strip()}"' for kw in all_kw[:4] if kw.strip()]
                if formatted:
                    pillar_blocks.append(f"({' OR '.join(formatted)})")

        query_body = " AND ".join(pillar_blocks) if pillar_blocks else ""
        formatted_query = f"{search_fields}=({query_body})" if query_body else ""

        if self.ipc_classes:
            clean_ipcs = [re.sub(r'[\s;*]', '', c).strip().upper() for c in self.ipc_classes if c.strip()]
            if clean_ipcs:
                ipc_block = " OR ".join([f'"{code}"*' for code in clean_ipcs])
                if formatted_query:
                    formatted_query += f" AND IC=({ipc_block})"
                else:
                    formatted_query = f"IC=({ipc_block})"

        return formatted_query.rstrip("; ").strip()

    def generate_report_text(self, claim_chart_df: pd.DataFrame = None, prior_art_data: dict = None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "=" * 85,
            f"專利檢索與技術特徵分析報告 (含 Claims 檢核表、比對矩陣與前案附錄)",
            f"產出時間：{now}",
            "=" * 85,
            f"\n【一、發明標的與分類設定】",
            f"標的名稱：{self.target_title}",
            f"IPC 分類號：{', '.join(self.ipc_classes) if self.ipc_classes else '未指定'}",
            f"CPC 分類號：{', '.join(self.cpc_classes) if self.cpc_classes else '未指定'}",
            f"\n【二、技術特徵三支柱展開】",
        ]
        
        for idx, p in enumerate(self.pillars, 1):
            lines.append(f"  {idx}. {p.name}")
            lines.append(f"     - 英文關鍵字：{', '.join(p.en_keywords) if p.en_keywords else '無'}")
            lines.append(f"     - 中文關鍵字：{', '.join(p.zh_keywords) if p.zh_keywords else '無'}")

        lines.extend([
            f"\n【三、各平台布林檢索邏輯式】",
            f"▶ Google Patents / Espacenet 檢索語法：",
            f"{self.to_google_patents_query()}\n",
            f"▶ 台灣智慧財產局 (GPSS) 檢索語法：",
            f"{self.to_gpss_query()}",
            f"\n" + "=" * 85,
            f"【四、申請專利範圍（Claims）初稿撰寫合規檢核表】",
            f"=" * 85,
            f"[ ] 1. 標的定性清楚：獨立項前言（Preamble）是否清楚載明法定標的類型（物/裝置/系統/方法）？",
            f"[ ] 2. 過渡詞適切性：是否優先採用開放式過渡詞「包含（comprising）」，避免非必要之閉鎖限制？",
            f"[ ] 3. 獨立項最小特徵原則：獨立項（Claim 1）是否只記載達成發明目的之必要技術特徵，未塞入非必要優化參數？",
            f"[ ] 4. 名詞前置依據（Antecedent Basis）：所有冠上「該（the/said）」之元件，先前是否皆有一致之首次引入（一...）？",
            f"[ ] 5. 附屬項層級防禦：附屬項是否由寬至窄收斂，為進步性答辯與被核駁時預留明確退路（Fallback Positions）？",
            f"[ ] 6. 功效用語避免：專利範圍主體中是否避免出現「達到省電優勢」、「可增加30%效率」等純功效/宣傳性字眼？",
            f"\n" + "=" * 85,
            f"【五、全要件原則（All-Elements Rule）前案比對分析矩陣】",
            f"=" * 85,
        ])

        if claim_chart_df is not None and not claim_chart_df.empty:
            lines.append(claim_chart_df.to_string(index=False))
        else:
            lines.append("（尚未建立比對要件資料）")

        lines.extend([
            f"\n\n【比對結論備註】：",
            f"1. 字面侵權/缺乏新穎性判定：前案是否完全讀取了本發明 Claim 1 的所有 Element？",
            f"2. 進步性差異點（Distinguishing Features）：標註出哪一個 Element 具備非顯而易知性之技術突破。",
            "=" * 85,
            f"\n【六、引證前案原文摘錄（附錄 Appendix）】",
            "=" * 85,
        ])

        if prior_art_data:
            lines.extend([
                f"專利號碼：{prior_art_data.get('patent_no', '未知')}",
                f"專利名稱：{prior_art_data.get('title', '未知')}",
                f"線上來源：{prior_art_data.get('url', '未知')}",
                f"\n--- 說明書摘要 (Abstract) ---",
                f"{prior_art_data.get('abstract', '無摘要內容')}",
                f"\n--- 申請專利範圍原文 (Claims) ---",
                f"{prior_art_data.get('claims', '無 Claims 內容')}",
                "=" * 85
            ])
        else:
            lines.extend([
                "（本次分析未執行線上前案專利爬取，或尚未載入引證專利原文資料）",
                "=" * 85
            ])

        return "\n".join(lines)

# ==============================================================================
# 二、 專利號爬取核心
# ==============================================================================
def fetch_patent_data_from_google(patent_no: str) -> dict:
    clean_pno = re.sub(r'[\s\-_/]', '', patent_no).upper()
    if clean_pno.startswith(("CN", "TW")):
        url = f"https://patents.google.com/patent/{clean_pno}"
    else:
        url = f"https://patents.google.com/patent/{clean_pno}/en"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    resp = requests.get(url, headers=headers, timeout=12)
    if resp.status_code != 200:
        raise Exception(f"無法取得專利資料 (HTTP {resp.status_code})，請確認專利號碼是否正確。")

    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.content, "html.parser", from_encoding="utf-8")

    title_elem = soup.find("meta", {"name": "DC.title"})
    title = title_elem["content"].strip() if title_elem and "content" in title_elem.attrs else ""
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else clean_pno

    abstract_sec = soup.find("section", {"itemprop": "abstract"})
    abstract = abstract_sec.get_text(separator="\n", strip=True) if abstract_sec else "（未擷取到摘要內容）"

    claims_sec = soup.find("section", {"itemprop": "claims"})
    claims_text = claims_sec.get_text(separator="\n", strip=True) if claims_sec else ""
    if not claims_text:
        c_elems = soup.find_all("div", class_="claim-text")
        claims_text = "\n".join([c.get_text(strip=True) for c in c_elems[:10]])

    return {
        "patent_no": clean_pno,
        "title": title,
        "abstract": abstract[:3500],
        "claims": claims_text[:7000],
        "url": url
    }

# ==============================================================================
# 三、 商標圖樣繪製核心邏輯
# ==============================================================================
def get_custom_font(font_size: int):
    candidate_fonts = [
        "NotoSansTC-Regular.ttf",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/msjhbd.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    ]
    for f in candidate_fonts:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, font_size)
            except Exception:
                continue

    local_font_path = "NotoSansTC-Regular.ttf"
    if not os.path.exists(local_font_path):
        try:
            font_url = "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf"
            r = requests.get(font_url, timeout=10)
            if r.status_code == 200:
                with open(local_font_path, "wb") as f_out:
                    f_out.write(r.content)
                return ImageFont.truetype(local_font_path, font_size)
        except Exception:
            pass

    return ImageFont.load_default()

def create_tipo_trademark_bytes(
    text: str,
    layout: str = "純文字模式",
    font_size: int = 68,
    text_align: str = "置中對齊",
    line_spacing_ratio: float = 0.35,
    logo_file=None
) -> bytes:
    dpi = 300
    cm_to_inch = 2.54
    width_px = int((8.0 / cm_to_inch) * dpi)
    height_px = int((8.0 / cm_to_inch) * dpi)

    canvas = Image.new("RGB", (width_px, height_px), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font = get_custom_font(font_size)

    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    if not lines:
        lines = [""]

    line_bboxes = []
    line_widths = []
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        line_bboxes.append(bbox)
        line_widths.append(lw)
        line_heights.append(lh)

    line_spacing_px = int(font_size * line_spacing_ratio)
    total_text_h = sum(line_heights) + line_spacing_px * (len(lines) - 1)
    max_line_w = max(line_widths) if line_widths else 0

    logo_img = None
    if logo_file is not None:
        try:
            uploaded_logo = Image.open(logo_file)
            if uploaded_logo.mode in ("RGBA", "LA") or (uploaded_logo.mode == "P" and "transparency" in uploaded_logo.info):
                rgba_logo = uploaded_logo.convert("RGBA")
                white_bg = Image.new("RGBA", rgba_logo.size, (255, 255, 255, 255))
                logo_img = Image.alpha_composite(white_bg, rgba_logo).convert("RGB")
            else:
                logo_img = uploaded_logo.convert("RGB")
        except Exception:
            logo_img = None

    def draw_multiline_block(start_top_y: int, block_center_x: int, block_w: int):
        cur_y = start_top_y
        for i, line in enumerate(lines):
            lw = line_widths[i]
            lh = line_heights[i]
            bbox = line_bboxes[i]

            if text_align == "靠左對齊":
                tx = block_center_x - (block_w // 2) - bbox[0]
            elif text_align == "靠右對齊":
                tx = block_center_x + (block_w // 2) - lw - bbox[0]
            else:
                tx = block_center_x - (lw // 2) - bbox[0]

            draw.text((tx, cur_y - bbox[1]), line, font=font, fill=(0, 0, 0))
            cur_y += lh + line_spacing_px

    if logo_img and layout == "複合商標：上圖下文":
        target_logo_h = int(height_px * 0.42)
        aspect = logo_img.width / logo_img.height
        new_w = int(target_logo_h * aspect)
        if new_w > int(width_px * 0.75):
            new_w = int(width_px * 0.75)
            target_logo_h = int(new_w / aspect)
        resized_logo = logo_img.resize((new_w, target_logo_h), Image.Resampling.LANCZOS)

        spacing = int(height_px * 0.04)
        total_block_h = target_logo_h + spacing + total_text_h
        start_y = (height_px - total_block_h) // 2

        logo_x = (width_px - new_w) // 2
        canvas.paste(resized_logo, (logo_x, start_y))

        text_start_y = start_y + target_logo_h + spacing
        draw_multiline_block(text_start_y, width_px // 2, max_line_w)

    elif logo_img and layout == "複合商標：左圖右文":
        target_logo_w = int(width_px * 0.35)
        aspect = logo_img.height / logo_img.width
        new_h = int(target_logo_w * aspect)
        if new_h > int(height_px * 0.6):
            new_h = int(height_px * 0.6)
            target_logo_w = int(new_h / aspect)
        resized_logo = logo_img.resize((target_logo_w, new_h), Image.Resampling.LANCZOS)

        spacing = int(width_px * 0.04)
        total_block_w = target_logo_w + spacing + max_line_w
        start_x = (width_px - total_block_w) // 2

        logo_y = (height_px - new_h) // 2
        canvas.paste(resized_logo, (logo_x, logo_y))

        text_center_x = start_x + target_logo_w + spacing + (max_line_w // 2)
        text_start_y = (height_px - total_text_h) // 2
        draw_multiline_block(text_start_y, text_center_x, max_line_w)

    else:
        start_y = (height_px - total_text_h) // 2
        draw_multiline_block(start_y, width_px // 2, max_line_w)

    img_buffer = io.BytesIO()
    canvas.save(img_buffer, format="JPEG", dpi=(dpi, dpi), quality=95, subsampling=0)
    return img_buffer.getvalue()

# ==============================================================================
# 四、 剪貼簿複製元件
# ==============================================================================
def render_copy_button(text_to_copy: str, button_label: str = "📋 點擊複製", button_id: str = "copyBtn"):
    escaped_text = text_to_copy.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    html_code = f"""
    <div style="margin-bottom: 10px;">
        <button id="{button_id}" style="
            width: 100%;
            background-color: #f0f2f6;
            color: #31333F;
            border: 1px solid #d6d6d8;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        ">{button_label}</button>
    </div>
    <script>
        const btn_{button_id} = document.getElementById("{button_id}");
        btn_{button_id}.addEventListener("click", async () => {{
            try {{
                await navigator.clipboard.writeText(`{escaped_text}`);
                btn_{button_id}.innerText = "✅ 已複製至剪貼簿！";
                btn_{button_id}.style.backgroundColor = "#e6f4ea";
                btn_{button_id}.style.color = "#137333";
                setTimeout(() => {{
                    btn_{button_id}.innerText = "{button_label}";
                    btn_{button_id}.style.backgroundColor = "#f0f2f6";
                    btn_{button_id}.style.color = "#31333F";
                }}, 2000);
            }} catch (err) {{
                console.error("複製失敗:", err);
            }}
        }});
    </script>
    """
    components.html(html_code, height=50)

# ==============================================================================
# 五、 智財法規與標準法律範本資料庫
# ==============================================================================
IP_LAWS_DB = [
    {
        "category": "專利法",
        "article": "專利法 第 21 條",
        "title": "發明之定義",
        "keywords": "自然法則, 技術思想, 發明",
        "text": "本法所稱發明，指利用自然法則之技術思想之創作。",
        "explanation": "發明必須是「利用自然法則」之技術創作。純粹之數學公式、商業模式或非利用自然法則者，無法取得發明專利。"
    },
    {
        "category": "專利法",
        "article": "專利法 第 22 條",
        "title": "專利三要件（產業利用性、新穎性、進步性）",
        "keywords": "新穎性, 進步性, 產業利用性, 公開, 容易完成",
        "text": "可供產業上利用之發明，無下列情事之一，得依本法申請專利：\n一、申請前已見於刊物者。\n二、申請前已公開實施者。\n三、申請前已為公眾所知悉者。\n\n發明雖無前項各款所列情事，但為其所屬技術領域中具有通常知識者依申請前之先前技術所能輕易完成時，仍不得依本法申請專利。",
        "explanation": "【實務要點】第1項規範「新穎性」；第2項規範「進步性」（通常知識者無法依多份前案結合輕易完成）。"
    },
    {
        "category": "專利法",
        "article": "專利法 第 26 條",
        "title": "說明書之充分揭露與申請專利範圍之明確性",
        "keywords": "說明書, 申請專利範圍, 明確, 充分揭露, 支持",
        "text": "說明書應明確且充分揭露，使該發明所屬技術領域中具有通常知識者，能瞭解其內容，並可據以實現。\n申請專利範圍應界定申請專利之發明；其得包括一項以上之請求項，各請求項應以明確、簡潔之方式記載，且必須為說明書所支持。",
        "explanation": "說明書必須達到「可據以實現（Enablement）」門檻，否則將依本條核駁或撤銷專利。"
    },
    {
        "category": "專利法",
        "article": "專利法 第 58 條",
        "title": "專利權人之排他專有權限",
        "keywords": "專利權, 排他權, 製造, 販賣, 使用, 輸入",
        "text": "專利權人，除本法另有規定外，專有排除他人未經其同意而製造、為販賣之要約、販賣、使用或為上述目的而進口該發明之權。",
        "explanation": "專利權本質上為「排除他人未經同意實施」之消極排他權。"
    },
    {
        "category": "化學配方專利專題",
        "article": "專利法 第 22 條 審查基準",
        "title": "化學組成物配方之進步性判定（協同效應）",
        "keywords": "化學配方, 協同效應, Synergistic Effect, 突變性增益",
        "text": "化學組成物若由已知成分混合而成，原則上視為先前技術之通常替換。\n惟若特定配比範圍內能產生「協同效應（Synergistic Effect）」或「無法預期之技術功效（Unexpected Results）」，且非通常知識者依既有理論所能預測者，應認定具備進步性。",
        "explanation": "【實務防禦】化學配方答辯核駁時，必須提出實驗數據證明 A+B 在特定比例下的功效遠大於各成分單獨效果相加。"
    },
    {
        "category": "營業秘密法",
        "article": "營業秘密法 第 2 條",
        "title": "營業秘密之法定三要件",
        "keywords": "營業秘密, 秘密性, 經濟價值, 合理保密措施",
        "text": "本法所稱營業秘密，指方法、技術、製程、配方、程式、設計或其他可用於生產、銷售或經營之資訊，而符合下列要件者：\n一、非一般涉及該類資訊之人所知者（非知悉性 / 秘密性）。\n二、因其秘密性而具有實際或潛在之經濟價值者（經濟價值性）。\n三、所有人已採取合理之保密措施者（合理保密措施）。",
        "explanation": "法院判定配方是否受保護，核心在於所有人是否採取「合理保密措施」（如進料代號化、權限分級、門禁與簽署 NDA）。"
    },
    {
        "category": "營業秘密法",
        "article": "營業秘密法 第 13 條之 1",
        "title": "侵害營業秘密之刑事責任（境內洩密罪）",
        "keywords": "刑事責任, 刑責, 竊取, 五年以下有期徒刑",
        "text": "意圖為自己或第三人不法之利益，或損害營業秘密所有人之利益，以竊取、毀損、隱匿、未經授權重製或取得，或知悉後擅自使用、洩漏者，處五年以下有期徒刑或拘役，得併科新臺幣一百萬元以上一千萬元以下罰金。",
        "explanation": "離職員工未經授權帶走配方表或實驗日誌，即構成非告訴乃論之刑事公訴罪。"
    },
    {
        "category": "商標法",
        "article": "商標法 第 18 條",
        "title": "商標之定義與識別性基本原則",
        "keywords": "商標, 識別性, 表彰, 商品, 服務",
        "text": "商標，指任何具有識別性之標識，得以文字、圖形、記號、顏色、立體形狀等組成。\n前項所稱識別性，指足以使商品或服務之相關消費者認識為指示商品或服務來源，並得與他人之商品或服務相區別者。",
        "explanation": "商標必須具備識別性，能讓公眾辨識產製來源。"
    }
]

USER_MANUAL_MARKDOWN = """# 📖 智慧財產權整合工作台 操作手冊（純本機離線旗艦版）

本工作台專為發明人、專利代理人、RD 研發工程師與企業法務設計，採用 **100% 純本機運算架構**，無須綁定任何外部付費 API。

---

## 模組一：📄 專利檢索與 Claims 比對矩陣
1. **即時自動推導**：在側邊欄或主畫面輸入關鍵字（如 `植物病蟲害光譜分析`、`氫燃料低壓儲存罐`），按下 Enter 立即重繪帶入 IPC、三支柱與 Claims！
2. **前案爬取**：輸入專利號（如 `US8608931B2`），直接以 UTF-8 抓取 Abstract 與 Claims 原文。
3. **全要件比對矩陣**：點擊「⚡ 一鍵自動帶入比對矩陣」即可將當前引證案動態配入表格與申復書中。
4. **防禦退路併入**：支援一鍵將附屬項（R705/R531 聯鎖波段）併入獨立項作為進步性防線。
5. **檢索式生成**：產出符合 Google Patents（無結尾分號、短核心片語）與 GPSS 規範的檢索式。
"""

TRADE_SECRET_AGREEMENT_DOC = """營業秘密保密暨離職切結書

立切結書人：＿＿＿＿＿＿＿＿＿＿（以下簡稱「乙方」）
身分證統一編號：＿＿＿＿＿＿＿＿＿＿
原任職部門／職稱：＿＿＿＿＿＿＿＿＿＿／＿＿＿＿＿＿＿＿＿＿
離職生效日期：中華民國＿＿＿年＿＿＿月＿＿＿日

緣乙方原受僱於＿＿＿＿＿＿＿＿＿＿股份有限公司（以下簡稱「甲方」），於任職期間因職務需要，知悉、接觸或取得甲方之各項機密技術與商業資訊。現因乙方於上述生效日終止與甲方之勞動契約，為釐清權益並恪遵法律規範，特立此切結書，承諾並恪遵下列條款：

--------------------------------------------------------------------------------
第一條：營業秘密之具體範圍與標的
--------------------------------------------------------------------------------
乙方明確知悉並承認，其於任職期間所接觸、知悉或產生之下列資訊，均屬《營業秘密法》第二條所保護之甲方核心營業秘密：
1. 配方與物化技術參數：特種塗料、電鍍添加劑、表面處理劑之組成物原料配比、特定分子結構、電位極化曲線。
2. 製程與投料工藝：特定反應溫度、剪切攪拌轉速、熟化時間、加料順序、母液預混調配方法。
3. 去識別化機制：原料虛擬編號對照表、獨家供應商名單、進貨成本及客製化規格要求。
4. 研發成果：實驗筆記（Lab Notebook）、未公開之實施例與比較例數據、專利初稿。

--------------------------------------------------------------------------------
第二條：保密義務與不作為承諾
--------------------------------------------------------------------------------
1. 嚴格保密：乙方自離職日起，非經甲方書面同意，絕不洩漏、交付、公開或使第三人知悉前條所述營業秘密。
2. 禁止不法使用：絕不為自己或任何第三人實施、調配、複製或利用前條所述任何配方或製程技術。
3. 終身保密：保密義務不因勞動契約終止或身分變更而消滅。

--------------------------------------------------------------------------------
第三條：刑事法令特別警示
--------------------------------------------------------------------------------
1. 依《營業秘密法》第十三條之一，竊取、擅自重製或洩漏營業秘密者，處五年以下有期徒刑，得併科新臺幣一百萬元以上一千萬元以下罰金。
2. 依《營業秘密法》第十三條之二，意圖在外國、大陸地區、香港或澳門使用而犯罪者，處一年以上十年以下有期徒刑，得併科新臺幣三百萬元以上五千萬元以下罰金。

立切結書人（乙方）簽章：＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
身分證字號：＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
中華民國    年    月    日
"""

# ==============================================================================
# 六、 本地智財語意推理與申復答辯理由書產生引擎
# ==============================================================================
def generate_advanced_oa_response(title: str, d1_pno: str, is_fallback_merged: bool = False) -> str:
    """純本機動態生成專利代理人規格之進步性申復理由書（含 D1+D2 結合阻礙與退路主張）"""
    low_t = title.lower()
    now_date = datetime.now().strftime("%Y 年 %m 月 %d 日")
    
    if any(k in low_t for k in ["光譜", "病蟲害", "作物", "植物"]):
        fallback_note = ""
        if is_fallback_merged:
            fallback_note = """
【特別補充：請求項第 1 項主動併入附屬項特徵，強化防禦退路（Fallback Position）】
為使本案能儘速獲准專利，申請人特將原附屬項中「特定窄波段 R705（紅邊）與 R531（光化學反射指數）比值聯鎖運算」及「環境光動態白平衡校正模組」之技術特徵併入獨立項第 1 項中。
此舉使本案之保護範疇更加收斂且具備不可動搖之進步性壁壘，引證案 D1 與 D2 通篇完全未曾揭露上述特定微觀生理波段之配比反饋。
"""
        return f"""專利申復理由書（草稿）

案  號：第 [請填入專利申請案號] 號
申 請 人：[請填入申請人/專利權人名稱]
發明名稱：{title}
受 文 者：經濟部智慧財產局

--------------------------------------------------------------------------------
一、 案由與前言聲明
--------------------------------------------------------------------------------
本件專利申請案業經 貴局審查官惠示審查意見通知函，認本案申請專利範圍請求項第 1 項等技術特徵，為所屬技術領域中具有通常知識者結合引證案 D1（{d1_pno}，多光譜影像裝置）與引證案 D2（常規植物病斑辨識技術）所能輕易置換思及完成，而有違反《專利法》第 22 條第 2 項（進步性）之虞。

申請人經研析引證文獻後，謹陳明：引證案 D1 與 D2 之間存在實質之「技術偏見與結合阻礙（Teaching Away）」，且本案達成「提前 48 至 72 小時無徵狀預警」之突變性協同功效，非通常知識者所能輕易思及。
{fallback_note}
--------------------------------------------------------------------------------
二、 審查基準法理依據
--------------------------------------------------------------------------------
按《專利法》第 22 條第 2 項規定，發明雖無同條第 1 項各款所列情事，但為其所屬技術領域中具有通常知識者依申請前之先前技術所能輕易完成時，仍不得依本法申請專利。

次按 貴局頒布之《專利審查基準》第二篇第三章「進步性之判斷」明載：
1. 判斷多份先前技術是否易於結合時，應考量先前技術間是否存有反向教示或技術阻礙（Teaching Away）；若先前技術之結合將導致其原有用圖失效或彼此牴觸者，通常知識者即不具結合動機。
2. 發明限定之特定元件配置，若產生超越各元件單純功效相加、非通常知識者依既有理論所能預測之突變性增益或協同效果（Unexpected Results）者，應認定具備進步性。

--------------------------------------------------------------------------------
三、 爭點具體比對與實體答辯理由
--------------------------------------------------------------------------------
（一） 支柱 A（Target）：引證案 D2 依存於「顯性可見病斑」，與本案「無徵狀潛伏期」存在本質技術斷層
1. 引證案 D2 所揭示之病害識別系統，其核心演算法完全架構於「可見色差邊緣輪廓（Morphological Contours）」與壞死斑點之圖形切割。
2. 惟本案請求項第 1 項鎖定之標的為「感染初期無徵狀（Asymptomatic Stage）」之葉片組織。在此階段，葉片表面肉眼可見特徵均屬正常，引證案 D2 所載之輪廓偵測運算將完全陷入失效（Technically Inoperable）。通常知識者根本無動機將依賴外觀病斑之 D2 與 D1 結合來處理無病徵葉片。

（二） 支柱 B（Mechanism）：引證案通篇未曾揭露本案特定窄波段生理特徵演算法
1. 引證案 D1 僅為通用型全波段光譜成像硬體之任意列舉，未針對植物病理生理反應教示特徵波段；直接結合會引發嚴重的維度災難與反光雜訊。
2. 本案發明人經反覆實驗突破性發現：特定紅邊波段（R705）與光化學反射指數（R531）之比值突變，與病原菌侵入初期之細胞壁果膠分解具有直接對應性。引證案通篇未曾啟示該特定波段之篩選，審查意見顯屬倒果為因之後見之明（Hindsight Bias）。

（三） 支柱 C（Effect）：本案達成「提前 48 小時阻斷傳播鏈」之無法預期技術功效
引證案 D1+D2 充其量僅能做到發病後的被動辨識；本案實現了在病原潛伏期提前 48~72 小時之精準預警，使防治手段得以提前介入，此項時間維度上的質變飛躍，完全具備顯著進步性。

--------------------------------------------------------------------------------
四、 結論與懇請事項
--------------------------------------------------------------------------------
綜上所陳，本案申請專利範圍請求項第 1 項完全符合《專利法》第 22 條第 2 項規定之進步性要件。懇請 貴局審查官惠予賜准本案專利。

謹呈
經濟部智慧財產局 公鑒

申請人：[請填入申請人/專利代理人簽章]
日 期：中華民國 {now_date}
"""
    else:
        # 通用型進步性申復理由
        return f"""專利申復理由書（草稿）

案  號：第 [請填入申請案號] 號
申 請 人：[請填入申請人名稱]
發明名稱：{title}
受 文 者：經濟部智慧財產局

--------------------------------------------------------------------------------
一、 案由與前言聲明
--------------------------------------------------------------------------------
本件專利申請案業經 貴局審查官惠示審查意見通知函，認本案申請專利範圍請求項第 1 項等技術特徵，為通常知識者結合引證案 D1（{d1_pno}）等所能輕易思及完成，而有違反《專利法》第 22 條第 2 項（進步性）之虞。

申請人經研析後，謹陳明：引證案實質上並未揭露本案請求項第 1 項所界定之關鍵技術特徵（Element 1C），更未教示該特定方案能誘發突變性協同增效（Synergistic Effect）。本案依法自具進步性。

--------------------------------------------------------------------------------
二、 爭點具體比對與實體答辯理由
--------------------------------------------------------------------------------
（一） 引證案未曾揭露本案特定核心限制特徵（Element 1C）
引證案僅為常規技術元件之教示，未限定各組分之精確動態切換或相互作用機制，通篇存在技術偏見或反向教示。

（二） 本案特定架構產生無法預期之「協同功效」
本案發明人經反覆實驗突破性發現，當主要組分被嚴格鎖定於本案限定架構時，產生了突變性控制與穩定性功效，使關鍵性能顯著提升。上述性質突變係屬典型的協同增效作用，完全具備進步性。

--------------------------------------------------------------------------------
三、 結論與懇請事項
--------------------------------------------------------------------------------
綜上所陳，本案完全符合《專利法》第 22 條第 2 項規定之進步性要件。懇請 貴局審查官惠予賜准本案專利。

謹呈
經濟部智慧財產局 公鑒

申請人：[請填入簽章]
日 期：中華民國 {now_date}
"""

def execute_semantic_synthesis(title: str):
    t = title.strip() if title.strip() else "自訂技術發明標的"
    low_t = t.lower()

    matched_ipc = []
    matched_p1_en, matched_p1_zh = [], []
    matched_p2_en, matched_p2_zh = [], []
    matched_p3_en, matched_p3_zh = [], []

    # 光譜分析 / 農業影像檢測專屬分支
    if any(k in low_t for k in ["光譜", "病蟲害", "作物", "植物"]):
        matched_ipc.extend(["G01N 21/84", "A01G 7/00", "G06V 20/10", "G06T 7/00"])
        p1_name = "Target: 農作物植株與早期病蟲害受損葉片"
        matched_p1_en = ["plant disease", "crop pest", "foliage pathogen", "leaf lesion"]
        matched_p1_zh = ["植物病害", "作物蟲害", "葉片病斑", "潛伏病原", "植株冠層"]
        p2_name = "Mechanism: 高光譜/多光譜成像與植被指數特徵提取演算法"
        matched_p2_en = ["hyperspectral imaging", "multispectral sensor", "vegetation index", "spectral reflectance"]
        matched_p2_zh = ["高光譜影像", "多光譜感測", "窄波段反射率", "植被指數(NDVI/PRI)", "特徵波段篩選"]
        p3_name = "Effect: 潛伏期無徵狀預警與病斑自動分類識別"
        matched_p3_en = ["asymptomatic detection", "early pest diagnosis", "disease classification", "real-time alert"]
        matched_p3_zh = ["潛伏期早期診斷", "無徵狀預警", "肉眼不可見病灶", "抑制擴散", "降低誤判率"]
        claim_elements = [
            {"要件編號": "Element 1A", "本案 Claim 1 技術要件": f"一種用於{t}之檢測系統，包含一多波段或高光譜光學採集感測器及移動載具", "前案 D1 對應技術": "[D1] 揭露通用型多光譜影像鏡頭總成", "前案 D2 對應技術": "", "符合性判定": "YES (字面讀取)", "差異/進步性說明": "提供光學反射數據採集結構。"},
            {"要件編號": "Element 1B", "本案 Claim 1 技術要件": "一邊緣影像運算單元，對特定波長反射光譜執行正規化降維校正並計算特定植被反射指數", "前案 D1 對應技術": "[D1] 揭露全光譜原始訊號輸出", "前案 D2 對應技術": "[D2] 揭露RGB可見光輪廓辨識", "符合性判定": "NO (不符/差異點)", "差異/進步性說明": "非可見光色彩分析，而是窄波段光譜指數生理運算。"},
            {"要件編號": "Element 1C", "本案 Claim 1 技術要件": "一光譜特徵病徵分類模型，根據水分與葉綠素吸收峰之微幅變異，在葉片表面肉眼顯性壞死前 24~72 小時識別無徵狀之潛伏病原感染", "前案 D1 對應技術": "[D1/D2] 未揭露無徵狀潛伏期光譜指紋映射", "前案 D2 對應技術": "[D2] 僅針對顯性可見病斑被動切割", "符合性判定": "NO (不符/差異點)", "差異/進步性說明": "【核心進步性防線】：引證案 D2 依存於可見病斑邊緣，在潛伏期完全失效；本案突破反向教示達成提前預警。"},
            {"要件編號": "Element 1D", "本案 Claim 1 技術要件": "一環境光自動補償白平衡校正板與定位分區即時通報模組", "前案 D1 對應技術": "[D1] 揭露一般白平衡校正鏡片", "前案 D2 對應技術": "", "符合性判定": "均等成立 (DOE)", "差異/進步性說明": "確保戶外強光下診斷強健性。"}
        ]
    elif any(k in low_t for k in ["儲存罐", "儲氫", "低壓儲存", "鋼瓶", "氣罐"]):
        matched_ipc.extend(["F17C 11/00", "C01B 3/00", "H01M 8/04"])
        p1_name = "Target: 固態儲氫合金低壓儲氣罐與供氫系統"
        matched_p1_en = ["low-pressure hydrogen tank", "metal hydride canister", "solid-state hydrogen storage"]
        matched_p1_zh = ["低壓儲氫罐", "儲氫合金瓶", "固態儲氫", "車載供氫系統"]
        p2_name = "Mechanism: 稀土金屬儲氫合金粉末與相變微熱管熱管理模組"
        matched_p2_en = ["metal hydride alloy", "micro-channel heat exchanger", "internal fin", "phase change material"]
        matched_p2_zh = ["AB5型儲氫合金", "鈦鐵系儲氫粉末", "內部翅片換熱管", "相變導熱模組", "過濾阻火器"]
        p3_name = "Effect: 5 MPa 以下超安全儲存與快速吸放氫動力學"
        matched_p3_en = ["low working pressure", "fast hydrogenation kinetics", "thermal runaway prevention"]
        matched_p3_zh = ["5 MPa以下低壓安全", "杜絕高壓爆破風險", "快速吸放氫", "導熱均衡防膨脹碎化"]
        claim_elements = [
            {"要件編號": "Element 1A", "本案 Claim 1 技術要件": f"一種用於{t}之儲氫容器，包含耐壓金屬外殼及內部氣體流道過濾閥件", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "YES (字面讀取)", "差異/進步性說明": "容器基本耐壓。"},
            {"要件編號": "Element 1B", "本案 Claim 1 技術要件": "一收容於內部之固態儲氫材料，包含金屬儲氫合金粉末", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "YES (字面讀取)", "差異/進步性說明": "低壓固態儲氫。"},
            {"要件編號": "Element 1C", "本案 Claim 1 技術要件": "內部高效換熱翅片與微熱管整合架構，工作壓力低於 5.0 MPa，吸放氫溫差小於 5°C", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "NO (不符/差異點)", "差異/進步性說明": "【核心進步性防線】：低壓安全且解決合金導熱不良。"},
            {"要件編號": "Element 1D", "本案 Claim 1 技術要件": "一彈性緩衝微孔隔層，吸收合金吸放氫應力", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "均等成立 (DOE)", "差異/進步性說明": "防膨脹板結。"}
        ]
    elif any(k in low_t for k in ["觸媒", "轉換器", "消氫", "氫燃料汽車"]):
        matched_ipc.extend(["B01D 53/94", "B01J 23/42", "H01M 8/04"])
        p1_name = "Target: 氫燃料電池汽車與排氣尾氣淨化系統"
        matched_p1_en = ["hydrogen fuel cell", "exhaust aftertreatment", "tailpipe emission"]
        matched_p1_zh = ["氫燃料電池汽車", "排氣後處理", "尾氣淨化", "陰極排氣"]
        p2_name = "Mechanism: 鉑鈀貴金屬低溫催化塗層與蜂窩載體"
        matched_p2_en = ["catalytic converter", "bimetallic catalyst", "honeycomb substrate"]
        matched_p2_zh = ["觸媒轉換器", "鉑鈀催化劑", "蜂窩載體", "低溫消氫塗層"]
        p3_name = "Effect: 超低溫消氫防爆燃與極致零排放"
        matched_p3_en = ["hydrogen mitigation", "explosion suppression", "low-temperature light-off"]
        matched_p3_zh = ["未反應殘氫消除", "防爆燃安全", "低起燃溫度", "耐水氣中毒"]
        claim_elements = [
            {"要件編號": "Element 1A", "本案 Claim 1 技術要件": f"一種用於{t}之催化淨化裝置，包含外殼及蜂窩載體", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "YES (字面讀取)", "差異/進步性說明": "排氣支撐。"},
            {"要件編號": "Element 1B", "本案 Claim 1 技術要件": "一負載於該載體表面之塗層，包含鉑(Pt)與鈀(Pd)雙金屬活性組分", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "YES (字面讀取)", "差異/進步性說明": "微量殘氫低溫氧化。"},
            {"要件編號": "Element 1C", "本案 Claim 1 技術要件": "40~90°C 排氣溫度下將氫氣濃度抑制於 1.0 vol% 以下之非爆炸極限", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "NO (不符/差異點)", "差異/進步性說明": "【核心進步性防線】：解決冷啟動低溫殘氫爆燃風險。"},
            {"要件編號": "Element 1D", "本案 Claim 1 技術要件": "疏水性抗水氣凝結層，防止陰極尾氣水淹中毒", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "均等成立 (DOE)", "差異/進步性說明": "消除水淹。"}
        ]
    else:
        matched_ipc.append("G06F 17/00")
        p1_name = f"Target: {t} 工作單元"
        matched_p1_en = ["operation unit", "mechanical assembly", "target carrier"]
        matched_p1_zh = ["作業單元", "機構本體", "目標載體"]
        p2_name = "Mechanism: 核心功能模組與控制架構"
        matched_p2_en = ["functional module", "control architecture", "actuator system"]
        matched_p2_zh = ["核心功能模組", "控制架構", "致動系統"]
        p3_name = "Effect: 系統運作效能與穩定度提升"
        matched_p3_en = ["operational efficiency", "system stability", "precision enhancement"]
        matched_p3_zh = ["作業效能提升", "運作穩定度", "精度增益"]
        claim_elements = [
            {"要件編號": "Element 1A", "本案 Claim 1 技術要件": f"一種{t}，包含基礎承載機架", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "待確認", "差異/進步性說明": "基礎支撐。"},
            {"要件編號": "Element 1B", "本案 Claim 1 技術要件": "一核心作業單元配置於該機架", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "待確認", "差異/進步性說明": "核心構件。"},
            {"要件編號": "Element 1C", "本案 Claim 1 技術要件": "一動態反饋調控單元以提升穩定度", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "待確認", "差異/進步性說明": "【核心進步性防線】"},
            {"要件編號": "Element 1D", "本案 Claim 1 技術要件": "一輔助安全與防護模組", "前案 D1 對應技術": "", "前案 D2 對應技術": "", "符合性判定": "待確認", "差異/進步性說明": "安全防護。"}
        ]

    clean_ipc = ", ".join(list(dict.fromkeys(matched_ipc)))

    st.session_state["v_id"] = st.session_state.get("v_id", 0) + 1
    st.session_state["patent_data"] = {
        "title": t,
        "ipc": clean_ipc,
        "cpc": clean_ipc,
        "p1_name": p1_name,
        "p1_en": ", ".join(matched_p1_en),
        "p1_zh": ", ".join(matched_p1_zh),
        "p2_name": p2_name,
        "p2_en": ", ".join(matched_p2_en),
        "p2_zh": ", ".join(matched_p2_zh),
        "p3_name": p3_name,
        "p3_en": ", ".join(matched_p3_en),
        "p3_zh": ", ".join(matched_p3_zh),
        "claims": claim_elements
    }
    # 自動同步載入代理人規格申復答辯理由書
    st.session_state["last_oa_result"] = generate_advanced_oa_response(t, "US8608931B2", is_fallback_merged=False)

# ==============================================================================
# 七、 應用程式介面 (Streamlit 渲染)
# ==============================================================================
st.set_page_config(
    page_title="智慧財產權整合工作台 (離線旗艦版)",
    page_icon="🛡️",
    layout="wide"
)

if "v_id" not in st.session_state:
    st.session_state["v_id"] = 0

if "patent_data" not in st.session_state:
    execute_semantic_synthesis("植物病蟲害光譜分析")

if "last_fetched_patent" not in st.session_state:
    st.session_state["last_fetched_patent"] = None

st.title("🛡️ 智慧財產權整合工作台 (離線旗艦版)")
st.markdown("⚡ **100% 本地運行模式**：無須設定 API Key，整合 Google Patents 扁平化檢索式、專利號爬取、Claims 比對矩陣、進步性答辯產生器與 TIPO 規範圖樣生成。")

# 側邊欄
st.sidebar.title("🛠️ 工作台輔助面板")

with st.sidebar.expander("📖 操作手冊與使用說明", expanded=False):
    st.markdown(USER_MANUAL_MARKDOWN)
    st.download_button(
        label="📥 下載操作手冊 (.md)",
        data=USER_MANUAL_MARKDOWN,
        file_name="IP_Workbench_User_Manual.md",
        mime="text/markdown",
        use_container_width=True
    )

with st.sidebar.expander("🔒 營業秘密離職切結書範本", expanded=False):
    trade_secret_edit = st.text_area(
        "切結書內容（可線上微調）：",
        value=TRADE_SECRET_AGREEMENT_DOC,
        height=220,
        key="trade_secret_sidebar_area"
    )
    render_copy_button(trade_secret_edit, "📋 複製切結書全文", button_id="copyTradeSecretSidebar")
    cur_ts_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.download_button(
        label="📥 下載離職切結書 (.txt)",
        data=trade_secret_edit.encode("utf-8"),
        file_name=f"Trade_Secret_NDA_{cur_ts_time}.txt",
        mime="text/plain;charset=utf-8",
        use_container_width=True
    )

tab_patent, tab_trademark, tab_laws = st.tabs([
    "📄 專利檢索與 Claims 比對矩陣",
    "🏷️ 商標權佈局與圖樣生成器",
    "⚖️️ 智財法規速查 (專利法、商標法 ＆ 營業秘密法)"
])

# TAB 1: 專利權模組
with tab_patent:
    st.sidebar.markdown("---")
    st.sidebar.header("📁 快速切換熱門範本")
    
    col_sb1, col_sb2 = st.sidebar.columns(2)
    with col_sb1:
        if st.button("🌿 光譜病害分析", use_container_width=True):
            execute_semantic_synthesis("植物病蟲害光譜分析")
            st.rerun()
    with col_sb2:
        if st.button("🛢 低壓儲氫罐", use_container_width=True):
            execute_semantic_synthesis("氫燃料低壓儲存罐")
            st.rerun()

    col_sb3, col_sb4 = st.sidebar.columns(2)
    with col_sb3:
        if st.button("⚡ 氫能觸媒轉換", use_container_width=True):
            execute_semantic_synthesis("氫燃料汽車觸媒轉換器")
            st.rerun()
    with col_sb4:
        if st.button("🌱 土壤熱水殺菌", use_container_width=True):
            execute_semantic_synthesis("並聯瓦斯熱水器土壤殺菌")
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("💡 任意主題自動產生器")
    st.sidebar.caption("輸入技術名稱（點擊按鈕或按 Enter 即時連動）：")
    
    sidebar_query = st.sidebar.text_input("輸入名稱", placeholder="例如：植物病蟲害光譜分析", key="sb_query_box")
    if st.sidebar.button("🚀 即刻推導並連動", use_container_width=True):
        if sidebar_query.strip():
            execute_semantic_synthesis(sidebar_query.strip())
            st.rerun()

    cur_data = st.session_state["patent_data"]
    vid = st.session_state["v_id"]

    st.subheader("1. 發明標的名稱與分類號設定 (支援自由編輯)")
    col_input1, col_input2, col_input3 = st.columns([2, 1, 1])

    with col_input1:
        new_title = st.text_input("請輸入專利標的名稱（修改後按 Enter 即刻推導）：", value=cur_data["title"], key=f"title_in_{vid}")
        if new_title != cur_data["title"]:
            execute_semantic_synthesis(new_title)
            st.rerun()

    with col_input2:
        ipc_input = st.text_input("IPC 分類號 (逗號隔開)", value=cur_data["ipc"], key=f"ipc_in_{vid}")
        cur_data["ipc"] = ipc_input
        
    with col_input3:
        cpc_input = st.text_input("CPC 分類號 (逗號隔開)", value=cur_data["cpc"], key=f"cpc_in_{vid}")
        cur_data["cpc"] = cpc_input

    st.markdown("---")
    st.subheader("2. 技術三支柱特徵拆解 (Target / Mechanism / Effect)")

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.markdown("#### 支柱 A：應用標的 (Target)")
        p1_name = st.text_input("支柱 A 名稱", value=cur_data["p1_name"], key=f"p1n_{vid}")
        p1_en = st.text_area("英文關鍵字 (逗號隔開)", value=cur_data["p1_en"], height=90, key=f"p1e_{vid}")
        p1_zh = st.text_area("中文關鍵字 (逗號隔開)", value=cur_data["p1_zh"], height=90, key=f"p1z_{vid}")
        cur_data["p1_name"], cur_data["p1_en"], cur_data["p1_zh"] = p1_name, p1_en, p1_zh

    with col_p2:
        st.markdown("#### 支柱 B：核心手段 (Mechanism)")
        p2_name = st.text_input("支柱 B 名稱", value=cur_data["p2_name"], key=f"p2n_{vid}")
        p2_en = st.text_area("英文關鍵字 (逗號隔開)", value=cur_data["p2_en"], height=90, key=f"p2e_{vid}")
        p2_zh = st.text_area("中文關鍵字 (逗號隔開)", value=cur_data["p2_zh"], height=90, key=f"p2z_{vid}")
        cur_data["p2_name"], cur_data["p2_en"], cur_data["p2_zh"] = p2_name, p2_en, p2_zh

    with col_p3:
        st.markdown("#### 支柱 C：技術功效 (Effect)")
        p3_name = st.text_input("支柱 C 名稱", value=cur_data["p3_name"], key=f"p3n_{vid}")
        p3_en = st.text_area("英文關鍵字 (逗號隔開)", value=cur_data["p3_en"], height=90, key=f"p3e_{vid}")
        p3_zh = st.text_area("中文關鍵字 (逗號隔開)", value=cur_data["p3_zh"], height=90, key=f"p3z_{vid}")
        cur_data["p3_name"], cur_data["p3_en"], cur_data["p3_zh"] = p3_name, p3_en, p3_zh

    st.markdown("---")
    st.subheader("3. 引證前案專利號爬取 (直連 Google Patents 原文)")
    col_fetch1, col_fetch2 = st.columns([3, 1])
    with col_fetch1:
        target_pno = st.text_input("前案專利號 (公開號/公告號)：", placeholder="例如：US11578418B2、CN110016700A 或 US8608931B2", key="fetch_pno_input")
    with col_fetch2:
        st.write("")
        st.write("")
        fetch_btn = st.button("📥 爬取前案專利內容", type="secondary", use_container_width=True)

    if fetch_btn:
        if not target_pno.strip():
            st.warning("請先輸入前案專利號。")
        else:
            with st.spinner(f"🌐 正在爬取 {target_pno.strip()} 專利內容..."):
                try:
                    p_data = fetch_patent_data_from_google(target_pno.strip())
                    st.session_state["last_fetched_patent"] = p_data
                    st.success(f"✅ 成功擷取專利：【{p_data['patent_no']}】{p_data['title']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"爬取失敗: {e}")

    cur_pno = target_pno.strip().upper() if target_pno.strip() else "US8608931B2"

    if st.session_state["last_fetched_patent"]:
        last_p = st.session_state["last_fetched_patent"]
        with st.expander(f"📖 查看最近爬取之專利原文：【{last_p['patent_no']}】{last_p['title']}", expanded=True):
            col_info1, col_info2 = st.columns([3, 1])
            with col_info1:
                st.markdown(f"**專利名稱**：{last_p['title']}")
                st.markdown(f"**專利公開/公告號**：`{last_p['patent_no']}`")
            with col_info2:
                st.link_button("🌐 在 Google Patents 開啟原文", last_p["url"], use_container_width=True)

            st.markdown("##### 📄 專利說明書摘要 (Abstract)")
            st.info(last_p["abstract"] if last_p["abstract"] else "無摘要內容")

            st.markdown("##### ⚖️ 申請專利範圍原文 (Claims)")
            if last_p["claims"]:
                st.code(last_p["claims"], language="text")
            else:
                st.warning("未自該專利頁面擷取到 Claims 條文。")

    st.markdown("---")
    st.subheader("4. 申請專利範圍全要件比對矩陣 (線上編輯)")
    current_claims = cur_data.get("claims", [])
    
    edited_df = st.data_editor(
        pd.DataFrame(current_claims),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "要件編號": st.column_config.TextColumn("要件編號", width="small", required=True),
            "本案 Claim 1 技術要件": st.column_config.TextColumn("本案 Claim 1 技術要件", width="medium"),
            "前案 D1 對應技術": st.column_config.TextColumn("前案 D1 對應技術", width="medium"),
            "前案 D2 對應技術": st.column_config.TextColumn("前案 D2 對應技術", width="medium"),
            "符合性判定": st.column_config.SelectboxColumn("符合性判定", options=["YES (字面讀取)", "NO (不符/差異點)", "均等成立 (DOE)", "待確認"], width="small"),
            "差異/進步性說明": st.column_config.TextColumn("差異分析 / 進步性技術功效", width="large"),
        },
        key=f"claim_editor_{vid}"
    )

    col_btn_oa1, col_btn_oa2 = st.columns(2)
    with col_btn_oa1:
        if st.button("⚖️ 帶入技術三支柱進步性答辯書 (D1+D2 結合阻礙)", use_container_width=True):
            st.session_state["last_oa_result"] = generate_advanced_oa_response(cur_data["title"], cur_pno, is_fallback_merged=False)
            st.success("✅ 已自動套入三支柱進步性答辯書！")
            st.rerun()

    with col_btn_oa2:
        if st.button("🛡️ 併入附屬項特徵作為防禦退路 (Fallback Position)", use_container_width=True):
            # 將附屬項特定波段聯鎖併入 Claim 1
            cur_data["claims"][2]["本案 Claim 1 技術要件"] += "，且該特定波段限定為 705 nm 紅邊與 531 nm 光化學反射指數 (PRI) 之比值聯鎖運算"
            cur_data["claims"][2]["差異/進步性說明"] = "【防禦退路突破】：主動限縮特定窄波段細胞壁降解特徵，徹底打破 D1+D2 之顯而易見性。"
            st.session_state["last_oa_result"] = generate_advanced_oa_response(cur_data["title"], cur_pno, is_fallback_merged=True)
            st.success("✅ 已將特定生理波段特徵併入獨立項，並更新答辯理由書！")
            st.rerun()

    if st.session_state.get("last_oa_result"):
        with st.expander("📄 檢視專利進步性答辯申復理由書（已整合三支柱與反向教示）", expanded=True):
            oa_display_text = st.session_state["last_oa_result"]
            st.text_area("申復理由書全文：", value=oa_display_text, height=350, key="quick_oa_preview_box")
            col_oa_copy, col_oa_dl = st.columns(2)
            with col_oa_copy:
                render_copy_button(oa_display_text, "📋 快速複製申復理由全文", button_id="copyQuickOA")
            with col_oa_dl:
                current_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    label="📥 下載申復理由書檔案 (.txt)",
                    data=oa_display_text.encode("utf-8"),
                    file_name=f"Patent_OA_Response_{current_timestamp}.txt",
                    mime="text/plain;charset=utf-8",
                    type="primary",
                    use_container_width=True
                )

    st.markdown("---")
    if st.button("🚀 生成專利檢索式並整合比對報告", type="primary", use_container_width=True):
        builder = PatentSearchBuilder(cur_data["title"])
        if cur_data["ipc"]:
            builder.add_ipc(*cur_data["ipc"].split(","))
        if cur_data["cpc"]:
            builder.add_cpc(*cur_data["cpc"].split(","))
        builder.add_pillar(cur_data["p1_name"], cur_data["p1_en"].split(",") if cur_data["p1_en"] else [], cur_data["p1_zh"].split(",") if cur_data["p1_zh"] else [])
        builder.add_pillar(cur_data["p2_name"], cur_data["p2_en"].split(",") if cur_data["p2_en"] else [], cur_data["p2_zh"].split(",") if cur_data["p2_zh"] else [])
        builder.add_pillar(cur_data["p3_name"], cur_data["p3_en"].split(",") if cur_data["p3_en"] else [], cur_data["p3_zh"].split(",") if cur_data["p3_zh"] else [])

        google_query = builder.to_google_patents_query()
        gpss_query = builder.to_gpss_query()
        
        report_text = builder.generate_report_text(
            claim_chart_df=edited_df,
            prior_art_data=st.session_state.get("last_fetched_patent")
        )

        st.subheader("📋 產出結果")
        col_res1, col_res2 = st.columns(2)
        with col_res1:
            st.markdown("#### 🌐 Google Patents 檢索式 (官方相容規範)")
            st.code(google_query if google_query else "（無有效檢索式）", language="text")
            if google_query.strip():
                render_copy_button(google_query, "📋 快速複製 Google Patents 檢索式", button_id="copyGoogle")
                encoded_query = urllib.parse.quote_plus(google_query)
                st.link_button("🌐 一鍵前往 Google Patents 檢索", f"https://patents.google.com/?q={encoded_query}", type="secondary", use_container_width=True)

        with col_res2:
            st.markdown("#### 🇹🇼 台灣智慧局 GPSS 檢索式")
            st.code(gpss_query if gpss_query else "（無有效檢索式）", language="text")
            if gpss_query.strip():
                render_copy_button(gpss_query, "📋 快速複製 GPSS 檢索式", button_id="copyGPSS")
                st.link_button("🇹🇼 開啟台灣智慧局 GPSS 系統", "https://gpss.tipo.gov.tw/", type="secondary", use_container_width=True)

        st.markdown("---")
        st.subheader("💾 匯出專利報告檔")
        time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_bytes = edited_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "📥 下載完整檢索分析報告 (.txt)",
                data=report_text,
                file_name=f"patent_analysis_{time_str}.txt",
                mime="text/plain",
                type="primary",
                use_container_width=True
            )

        with col_dl2:
            st.download_button(
                "📊 下載前案比對矩陣 (.csv)",
                data=csv_bytes,
                file_name=f"claim_chart_{time_str}.csv",
                mime="text/csv",
                type="secondary",
                use_container_width=True
            )

# TAB 2: 商標權模組
with tab_trademark:
    st.subheader("🏷️ TIPO 規範商標圖樣即時產生器 (8×8 cm @ 300 DPI)")
    col_tm1, col_tm2 = st.columns([1, 1])

    with col_tm1:
        tm_multiline_text = st.text_area(
            "圖樣文字內容（支援按下 Enter 自由換行）：",
            value="極光\nAurorix",
            height=85,
            help="可直接按 Enter 鍵進行多行換行排列。"
        )

        uploaded_logo = st.file_uploader("選填：上傳品牌 Logo 圖檔 (PNG/JPG，透明底自動填白)", type=["png", "jpg", "jpeg"])

        col_ctrl1, col_ctrl2 = st.columns(2)
        with col_ctrl1:
            layout_options = ["純文字模式"]
            if uploaded_logo is not None:
                layout_options = ["複合商標：上圖下文", "複合商標：左圖右文"] + layout_options
            layout_choice = st.selectbox("圖樣排版方式：", layout_options)

        with col_ctrl2:
            align_choice = st.selectbox("文字對齊方式：", ["置中對齊", "靠左對齊", "靠右對齊"])

        col_slider1, col_slider2 = st.columns(2)
        with col_slider1:
            font_size_val = st.slider("文字字級大小 (Font Size)：", min_value=28, max_value=120, value=58 if uploaded_logo else 68, step=2)
        with col_slider2:
            spacing_ratio_val = st.slider("行距倍率 (Line Spacing)：", min_value=0.1, max_value=1.5, value=0.35, step=0.05)

    with col_tm2:
        if tm_multiline_text.strip():
            img_bytes = create_tipo_trademark_bytes(
                text=tm_multiline_text.strip(),
                layout=layout_choice,
                font_size=font_size_val,
                text_align=align_choice,
                line_spacing_ratio=spacing_ratio_val,
                logo_file=uploaded_logo
            )
            st.image(img_bytes, caption="📸 圖樣預覽 (8x8 cm @ 300 DPI 標準白底)", width=320)

            clean_first_line = re.sub(r'[\r\n\s]+', '_', tm_multiline_text.strip()[:20])
            clean_filename = f"trademark_{clean_first_line}.jpg"
            st.download_button(
                label="📥 下載標準商標圖樣檔 (.jpg)",
                data=img_bytes,
                file_name=clean_filename,
                mime="image/jpeg",
                type="primary",
                use_container_width=True
            )
        else:
            st.warning("請先輸入商標文字以生成圖樣。")

# TAB 3: 智財法規速查
with tab_laws:
    st.subheader("⚖️ 智財法規速查指南 (含化學配方專利 ＆ 營業秘密法)")

    col_filter1, col_filter2 = st.columns([1, 2])
    with col_filter1:
        law_type_filter = st.selectbox(
            "篩選法規類別：",
            ["全部法規", "專利法", "化學配方專利專題", "營業秘密法", "商標法"]
        )
    with col_filter2:
        search_kw = st.text_input("輸入條文、標題或關鍵字過濾：", placeholder="例如：新穎性、協同效應、秘密性、合理保密措施")

    filtered_laws = IP_LAWS_DB
    if law_type_filter != "全部法規":
        filtered_laws = [item for item in filtered_laws if item["category"] == law_type_filter]

    if search_kw.strip():
        kw = search_kw.strip().lower()
        filtered_laws = [
            item for item in filtered_laws
            if kw in item["article"].lower() or kw in item["title"].lower() or kw in item["keywords"].lower() or kw in item["text"].lower()
        ]

    st.caption(f"共找到 {len(filtered_laws)} 則相關核心法規條文：")

    for item in filtered_laws:
        badge_map = {
            "專利法": "📄 專利法",
            "化學配方專利專題": "🧪 化學配方專題",
            "營業秘密法": "🔒 營業秘密法",
            "商標法": "🏷️ 商標法"
        }
        badge = badge_map.get(item["category"], "⚖️ 智財法規")
        expander_title = f"{badge} ｜ {item['article']}：{item['title']}"
        with st.expander(expander_title, expanded=True if search_kw.strip() else False):
            st.markdown(f"**🔍 關鍵字標籤**：`{item['keywords']}`")
            st.markdown("##### 📜 法定條文內容：")
            st.code(item["text"], language="text")
            st.markdown("##### 💡 審查實務、企業管理與答辯要點：")
            st.info(item["explanation"])

    st.markdown("---")
    st.markdown("#### 🌐 官方全國法規資料庫即時連結")
    col_ext1, col_ext2, col_ext3, col_ext4 = st.columns(4)
    with col_ext1:
        st.link_button("📜 中華民國《專利法》完整法條", "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=J0070007", use_container_width=True)
    with col_ext2:
        st.link_button("🔒 中華民國《營業秘密法》完整法條", "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=J0070028", use_container_width=True)
    with col_ext3:
        st.link_button("🏷️ 中華民國《商標法》完整法條", "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=J0070001", use_container_width=True)
    with col_ext4:
        st.link_button("🏛️ 智慧財產局專利/商標審查基準", "https://www.tipo.gov.tw/", use_container_width=True)
