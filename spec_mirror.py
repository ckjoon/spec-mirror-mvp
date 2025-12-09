import os
import re
import json
from notion_client import Client
from google import genai
from google.genai import types

# [Config] Free Tier King
MODEL_NAME = "gemini-2.5-flash" # -001 명시 (안전)

def main():
    # 1. Init & Auth
    notion_key = os.environ.get("NOTION_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")
    diff_path = os.environ.get("DIFF_FILE_PATH")
    
    # [New] Deep Linking을 위한 환경변수
    head_sha = os.environ.get("PR_HEAD_SHA")
    repo_name = os.environ.get("GITHUB_REPOSITORY") # owner/repo

    if not (notion_key and google_key and diff_path and head_sha):
        print("⛔ Error: Missing Environment Variables.")
        return

    notion = Client(auth=notion_key)
    # [Fix] API Version v1 강제
    client = genai.Client(api_key=google_key)

    # 2. Context Loading
    milestone_desc = os.environ.get("PR_MILESTONE_DESC", "")
    pr_number = os.environ.get("PR_NUMBER", "")
    
    print(f"🚀 Spec Mirror Auditing PR #{pr_number}")

    # 3. Read Diff
    try:
        with open(diff_path, "r", encoding="utf-8") as f:
            pr_diff = f.read()[:500000]
        if not pr_diff.strip():
            print("⚠️ Diff content is empty.")
            return
    except FileNotFoundError:
        print("⛔ Error: Diff file not found.")
        return

    # 4. Extract Specs (All Text Blocks)
    match = re.search(r"([a-f0-9]{32})", milestone_desc)
    if not match:
        print("⏭️ Skip: No Notion Page ID found in Milestone.")
        return
    page_id = match.group(1)

    try:
        blocks = notion.blocks.children.list(block_id=page_id)["results"]
    except Exception as e:
        print(f"⛔ Notion API Error: {e}")
        return

    block_map = {}
    spec_list_text = ""
    
    # [Upgrade] 지원하는 블록 타입 대폭 확장
    SUPPORTED_BLOCKS = [
        "to_do", 
        "bulleted_list_item", 
        "numbered_list_item", 
        "paragraph", 
        "heading_1", "heading_2", "heading_3",
        "toggle"
    ]

    for b in blocks:
        b_type = b["type"]
        if b_type in SUPPORTED_BLOCKS:
            rich_text = b[b_type].get("rich_text", [])
            if rich_text:
                text = "".join([t["plain_text"] for t in rich_text])
                b_id = b["id"]
                block_map[b_id] = text
                spec_list_text += f"- [ID: {b_id}] {text}\n"

    if not spec_list_text:
        print("⚠️ No specs found to audit.")
        return

    print(f"📋 Auditing {len(block_map)} specs (Text/Checkbox)...")

    # 5. Call Gemini (Ask for File & Lines)
    prompt = f"""
    You are a Senior Code Auditor. 
    Analyze the Git Diff and map implemented specs to specific code locations.

    [Specs]
    {spec_list_text}

    [Code Diff]
    {pr_diff}

    [Rules]
    1. Match only if the logic is strictly implemented.
    2. **Location:** Identify the 'file_path' and 'line_range' (e.g., "L10-L15") from the diff headers. 
       - Be precise. Use the line numbers from the 'new' version (the + side).
    3. **Summary:** Short technical summary (under 8 words).
    4. Output JSON format.
    """

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                # [Schema] 파일 경로와 라인 번호를 요구함
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "results": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "STRING"},
                                    "file_path": {"type": "STRING"},
                                    "line_range": {"type": "STRING"}, # 예: "L20-L25"
                                    "summary": {"type": "STRING"}
                                },
                                "required": ["id", "file_path", "line_range", "summary"]
                            }
                        }
                    },
                    "required": ["results"]
                }
            )
        )
        
        result = json.loads(response.text)
        matched_items = result.get("results", [])
        print(f"🤖 AI Verdict: {len(matched_items)} items verified.")

    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return

    # 6. Update Notion (Universal Block Updater)
    updated_cnt = 0
    for item in matched_items:
        b_id = item["id"]
        summary = item["summary"]
        file_path = item["file_path"]
        line_range = item["line_range"]

        if b_id in block_map:
            original_text = block_map[b_id]
            
            # 중복 방지 (이미 링크가 있으면 스킵)
            if "blob/" in original_text or "(PR #" in original_text:
                continue
            
            # http://clevelandconstruction.com/ GitHub Deep Link 생성
            # https://github.com/owner/repo/blob/sha/path/to/file#L10-L20
            # 라인 번호 포맷팅 (L10-L20 -> #L10-L20)
            if not line_range.startswith("#"):
                line_anchor = "#" + line_range.replace(":", "") # 안전장치
            else:
                line_anchor = line_range
            
            deep_link = f"https://github.com/{repo_name}/blob/{head_sha}/{file_path}{line_anchor}"

            # Notion Block Update Logic
            # 블록 타입을 다시 조회해서 그 타입에 맞게 업데이트 (to_do는 checked 추가, 나머지는 텍스트만)
            block_type = next((b["type"] for b in blocks if b["id"] == b_id), "paragraph")
            
            update_payload = {
                "rich_text": [
                    {"type": "text", "text": {"content": original_text}},
                    # [UI] 줄바꿈 + 📄 파일명:라인 + 요약
                    {
                        "type": "text", 
                        "text": {"content": f"\n   ↳ 📄 {file_path}:{line_range} - {summary}"}, 
                        "annotations": {"code": True, "color": "gray"},
                        "link": {"url": deep_link} # 여기에 딥링크 주입
                    }
                ]
            }

            # 체크박스인 경우에만 체크 처리
            if block_type == "to_do":
                update_payload["checked"] = True

            # 동적 키 할당 (block_type이 "paragraph"면 notion.blocks.update(..., paragraph=payload))
            kwargs = {block_type: update_payload}
            
            notion.blocks.update(block_id=b_id, **kwargs)
            
            print(f"✅ Linked ({block_type}): {file_path}:{line_range}")
            updated_cnt += 1

    print(f"🎉 Done. {updated_cnt} specs updated.")

if __name__ == "__main__":
    main()