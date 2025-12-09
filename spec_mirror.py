import os
import re
import json
from notion_client import Client
from google import genai
from google.genai import types

# 2025 Standard: 최신 SDK는 모델명 앞에 'models/'를 붙이지 않아도 되지만, 
# 명시적으로 gemini-1.5-pro 혹은 gemini-2.0-flash-exp 사용 권장
MODEL_NAME = "gemini-2.5-flash"

def main():
    # 1. Init & Auth
    notion_key = os.environ.get("NOTION_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")
    diff_path = os.environ.get("DIFF_FILE_PATH")
    
    if not (notion_key and google_key and diff_path):
        print("⛔ Error: Missing Environment Variables.")
        return

    notion = Client(auth=notion_key)
    # [New SDK] Client 초기화 방식 변경
    client = genai.Client(api_key=google_key)

    # 2. Context Loading
    milestone_desc = os.environ.get("PR_MILESTONE_DESC", "")
    pr_url = os.environ.get("PR_URL", "")
    pr_number = os.environ.get("PR_NUMBER", "")

    print(f"🚀 Spec Mirror (v2 with google-genai) Auditing PR #{pr_number}")

    # 3. Read Diff
    try:
        with open(diff_path, "r", encoding="utf-8") as f:
            pr_diff = f.read()[:500000]
    except FileNotFoundError:
        print("⛔ Error: Diff file not found.")
        return

    # 4. Extract Specs form Notion
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
    
    for b in blocks:
        if b["type"] in ["to_do", "bulleted_list_item"]:
            rich_text = b[b["type"]].get("rich_text", [])
            if rich_text:
                text = "".join([t["plain_text"] for t in rich_text])
                b_id = b["id"]
                block_map[b_id] = text
                spec_list_text += f"- [ID: {b_id}] {text}\n"

    if not spec_list_text:
        print("⚠️ No specs found to audit.")
        return

    print(f"📋 Auditing {len(block_map)} specs...")

    # 5. Call Gemini using [google-genai] SDK
    prompt = f"""
    You are a Code Auditor. Verify if the Specs are implemented in the Diff.

    [Specs]
    {spec_list_text}

    [Code Diff]
    {pr_diff}

    [Rules]
    1. Only include IDs where actual code logic implements the spec.
    2. Ignore comments or documentation changes.
    """

    try:
        # [New SDK] generate_content 호출 방식 변경
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                # [New SDK] Schema 정의 방식 (Dict 호환)
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "implemented_ids": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"}
                        }
                    },
                    "required": ["implemented_ids"]
                }
            )
        )
        
        # New SDK는 response.text로 바로 접근 가능
        result = json.loads(response.text)
        matched_ids = result.get("implemented_ids", [])
        print(f"🤖 AI Verdict: {len(matched_ids)} items verified.")

    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return

    # 6. Update Notion
    updated_cnt = 0
    for b_id in matched_ids:
        if b_id in block_map:
            original_text = block_map[b_id]
            if f"(PR #{pr_number})" in original_text:
                continue

            notion.blocks.update(
                block_id=b_id,
                to_do={
                    "checked": True,
                    "rich_text": [
                        {"type": "text", "text": {"content": original_text}},
                        {
                            "type": "text", 
                            "text": {"content": f" [PR #{pr_number}]", "link": {"url": pr_url}}, 
                            "annotations": {"code": True, "color": "blue"}
                        }
                    ]
                }
            )
            print(f"✅ Linked: {original_text[:20]}...")
            updated_cnt += 1

    print(f"🎉 Done. {updated_cnt} specs updated.")

if __name__ == "__main__":
    main()