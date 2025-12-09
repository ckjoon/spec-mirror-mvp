import os
import re
import json
import google.generativeai as genai
from notion_client import Client

# --- 설정 (Gemini 3.0 Pro) ---
# 2025년 기준 Google의 Flagship Model
# 압도적인 Context Window와 추론 능력을 사용합니다.
MODEL_NAME = "gemini-3.0-pro" 

def main():
    # 1. 인증 및 환경변수 확인
    try:
        notion = Client(auth=os.environ["NOTION_KEY"])
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    except KeyError as e:
        print(f"⛔ 환경변수 누락: {e}")
        return

    milestone_desc = os.environ.get("PR_MILESTONE_DESC", "")
    pr_diff = os.environ.get("PR_DIFF", "")
    pr_url = os.environ.get("PR_URL", "")
    pr_number = os.environ.get("PR_NUMBER", "")

    print(f"🚀 Spec Mirror 시작 (PR #{pr_number}) using {MODEL_NAME}")

    # 2. 마일스톤에서 Notion Page ID 추출
    match = re.search(r"notion\.so/(?:[^/]+/)?([a-f0-9]{32})", milestone_desc)
    if not match:
        print("⏭️ 마일스톤 설명에 Notion 링크가 없습니다. 스킵합니다.")
        return
    page_id = match.group(1)
    print(f"🎯 타겟 Notion Page ID: {page_id}")

    # 3. Notion 페이지의 체크박스/블렛 리스트 긁어오기
    try:
        blocks = notion.blocks.children.list(block_id=page_id)["results"]
    except Exception as e:
        print(f"⛔ Notion API 에러: {e} (봇을 페이지에 초대했나요?)")
        return

    block_map = {}
    spec_text = ""
    
    for b in blocks:
        # to_do(체크박스)와 bulleted_list_item(글머리)만 인식
        if b["type"] in ["to_do", "bulleted_list_item"]:
            b_type = b["type"]
            if b[b_type]["rich_text"]:
                text = b[b_type]["rich_text"][0]["plain_text"]
                b_id = b["id"]
                block_map[b_id] = text
                spec_text += f"ID: {b_id} | Spec: {text}\n"

    if not spec_text:
        print("⚠️ 동기화할 스펙(체크박스)이 페이지에 없습니다.")
        return

    print(f"📋 스펙 {len(block_map)}개 로드 완료. {MODEL_NAME} 분석 중...")

    # 4. Gemini 3.0 Pro에게 판결 맡기기
    try:
        model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            generation_config={"response_mime_type": "application/json"}
        )

        prompt = f"""
        You are a Senior Code Auditor.
        Analyze the Code Diff and identify which Spec Items are implemented.
        
        [Rules]
        1. Match only if the logic is explicitly present in the code.
        2. Return a JSON object with a list of matching IDs: {{"ids": ["id_1", "id_2"]}}
        
        [Spec Items]
        {spec_text}

        [Code Diff]
        {pr_diff} 
        """

        response = model.generate_content(prompt)
        matched_data = json.loads(response.text)
        matched_ids = matched_data.get("ids", [])
        print(f"🤖 {MODEL_NAME} 판단: {len(matched_ids)}개 항목 일치.")

    except Exception as e:
        # 혹시 3.0 API 접근 권한 문제 발생 시 로그 출력
        print(f"❌ Gemini API 분석 실패: {e}")
        print("💡 Tip: API 키 권한이나 모델명을 확인하세요.")
        return

    # 5. Notion 업데이트 (링크 주입)
    updated_count = 0
    for b_id in matched_ids:
        if b_id in block_map:
            original_text = block_map[b_id]
            
            # 이미 링크가 달려있으면 중복 업데이트 방지
            if "(PR #" in original_text:
                continue

            notion.blocks.update(
                block_id=b_id,
                to_do={ # 강제로 체크박스로 변환하고 체크 표시
                    "checked": True,
                    "rich_text": [
                        {"type": "text", "text": {"content": original_text}},
                        {"type": "text", "text": {"content": f" (PR #{pr_number})", "link": {"url": pr_url}}, "annotations": {"code": True, "color": "blue"}}
                    ]
                }
            )
            print(f"✅ 동기화 완료: {original_text[:30]}...")
            updated_count += 1
            
    print(f"🎉 총 {updated_count}개의 스펙이 업데이트되었습니다.")

if __name__ == "__main__":
    main()