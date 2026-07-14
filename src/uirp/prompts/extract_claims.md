---
name: extract_claims
version: 1
tier: M
output: json
description: Bóc Claim/Entity/Relationship từ một Observation, đúng như nguồn trình bày
---
Bạn bóc tách thông tin có cấu trúc từ nội dung dưới đây, ĐÚNG NHƯ NGUỒN TRÌNH BÀY.

QUY TẮC BẮT BUỘC:
- KHÔNG đánh giá đúng/sai/đáng tin. Chỉ ghi lại NGUỒN NÓI GÌ.
- Mỗi claim gắn chủ thể phát ngôn (asserted_by) nếu xác định được ai nói; không rõ thì null.
- Giữ NGUYÊN ngôn ngữ nguồn, KHÔNG dịch.
- confidence (0..1) là mức chắc chắn của việc BÓC TÁCH (máy đọc đúng chưa), KHÔNG phải độ tin nội dung.
- Nội dung nghi là sai VẪN bóc thành claim kèm chủ thể phát ngôn.

Nội dung:
"""
$text
"""

Chỉ trả về JSON đúng schema sau (không thêm chữ nào ngoài JSON):
{
  "claims": [
    {"statement": "mệnh đề nguồn nói", "asserted_by": "tên người/nguồn hoặc null", "confidence": 0.0}
  ],
  "entities": [
    {"name": "tên thực thể", "type": "person|organization|product|place|event|concept", "confidence": 0.0}
  ],
  "relationships": [
    {"subject": "tên", "predicate": "quan hệ", "object": "tên", "confidence": 0.0}
  ]
}
