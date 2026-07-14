---
name: classify_relevance
version: 1
tier: S
output: json
description: Lọc thô — nội dung có liên quan tới chủ đề nghiên cứu không (relevance, KHÔNG credibility)
---
Bạn đánh giá một đoạn nội dung có LIÊN QUAN tới chủ đề nghiên cứu "$topic" hay không.

QUY TẮC BẮT BUỘC:
- CHỈ đánh giá mức độ liên quan (relevance) tới chủ đề.
- TUYỆT ĐỐI KHÔNG đánh giá đúng/sai, thật/giả, hay độ tin cậy của nội dung.
- Nội dung ít người biết / gây tranh cãi / trái chiều VẪN có thể liên quan.

Nội dung:
"""
$text
"""

Chỉ trả về JSON: {"relevant": true} hoặc {"relevant": false}
