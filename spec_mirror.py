import os
import re
import json
import google.generativeai as genai
from notion_client import Client

# --- 설정 ---
# 2025 Context: Gemini 3.0 Pro (Real-world fallback: gemini-1.5-pro)
MODEL_NAME = "gemini-3.0-pro" 

def main():
    # 1. 환경변수 로드
    notion_key = os.environ.get("NOTION_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")
    diff_path = os.environ.get("DIFF_FILE_PATH")
    
    if not (notion_key and google_key and diff_path):
        print("⛔ 필수 환경변수 누락. Secrets를 확인하세요.")
        return

    # Notion & Gemini 초기화
    notion = Client(auth=notion_key)
    genai.configure(api_key=google_key)

    # Context 로드
    milestone_desc = os.environ.get("PR_MILESTONE_DESC", "")
    pr_url = os.environ.get("PR_URL", "")
    pr_number = os.environ.get("PR_NUMBER", "")

    print(f"🚀 Spec Mirror 가동: PR #{pr_number}")

    # 2. Diff 파일 읽기
    try:
        with open(diff_path, "r", encoding="utf-8") as f:
            pr_diff = f.read()
            # 토큰 절약을 위해 너무 큰 Diff는 앞부분만 (약 30만자)
            pr_diff = pr_diff[:300000] 
    except FileNotFoundError:
        print("⛔ Diff 파일을 찾을 수 없습니다.")
        return

    # 3. 마일스톤에서 Notion Page ID 추출
    # 포맷: https://notion.so/my-workspace/Page-Title-1234567890abcdef
    match = re.search(r"([a-f0-9]{32})", milestone_desc)
    if not match:
        print("⏭️ 마일스톤 설명에 Notion Page ID(32자리)가 없습니다. Skip.")
        return
    page_id = match.group(1)
    print(f"🎯 Target Notion Page: {page_id}")

    # 4. Notion 스펙 긁어오기 (Iterate Blocks)
    # 팁: 자식 블록이 많을 경우 pagination이 필요하지만 MVP에선 생략
    try:
        blocks = notion.blocks.children.list(block_id=page_id)["results"]
    except Exception as e:
        print(f"⛔ Notion API Error: {e}")
        return

    block_map = {}
    spec_list_text = ""
    
    for b in blocks:
        # 체크박스(to_do)와 불렛(bulleted_list_item)만 스펙으로 간주
        b_type = b["type"]
        if b_type in ["to_do", "bulleted_list_item"]:
            rich_text = b[b_type].get("rich_text", [])
            if rich_text:
                plain_text = "".join([t["plain_text"] for t in rich_text])
                
                # 이미 완료(체크)된 항목은 건너뛸까? -> 아니오, 구현 보강일 수 있으니 포함.
                # 단, 이미 링크가 달린 건 제외하려면 로직 추가 가능.
                
                block_map[b["id"]] = plain_text
                spec_list_text += f"- [ID: {b['id']}] {plain_text}\n"

    if not spec_list_text:
        print("⚠️ 분석할 스펙 항목이 없습니다.")
        return

    print(f"📋 {len(block_map)}개의 스펙 항목 분석 시작...")

    # 5. Gemini에게 심판 맡기기 (The Brain)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config={"response_mime_type": "application/json"}
    )

    prompt = f"""
    Role: Senior Technical Auditor.
    Task: Determine which 'Spec Items' have been meaningfully implemented or fixed in the provided 'Code Diff'.

    [Input Data]
    --- Spec Items ---
    {spec_list_text}
    
    --- Code Diff (Truncated) ---
    {pr_diff}

    [Strict Rules]
    1. **Evidence Based:** Only mark an ID as matched if you see specific code logic (functions, variables, tests) that implements the spec.
    2. **Ignore Comments:** Do not match if the spec is only mentioned in comments but not implemented.
    3. **Output Format:** JSON only. {{ "matched_ids": ["id_string_1", "id_string_2"] }}
    4. If no specs are implemented, return {{ "matched_ids": [] }}
    """

    try:
        response = model.generate_content(prompt)
        result = json.loads(response.text)
        matched_ids = result.get("matched_ids", [])
        print(f"🤖 AI Judgment: {len(matched_ids)} items implemented.")
    except Exception as e:
        print(f"❌ Gemini API Error: {e}")
        return

    # 6. Notion 업데이트 (Result Reflection)
    updated_count = 0
    for b_id in matched_ids:
        if b_id in block_map:
            original_text = block_map[b_id]
            
            # 이미 PR 링크가 있는지 확인 (중복 방지)
            if f"(PR #{pr_number})" in original_text:
                print(f"   Skip: {b_id} (Already linked)")
                continue

            # Notion Block Update
            # 주의: 블록 타입을 'to_do'로 강제 변경하면 체크박스가 생깁니다.
            notion.blocks.update(
                block_id=b_id,
                to_do={
                    "checked": True, # 구현되었으니 체크!
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
            print(f"✅ Verified & Linked: {original_text[:20]}...")
            updated_count += 1

    print(f"🎉 Spec Mirror Completed: {updated_count} specs updated.")

if __name__ == "__main__":
    main()